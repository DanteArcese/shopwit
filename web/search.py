"""The generic results page: held products matched by text, grouped by measure."""

from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.cache import patch_vary_headers

from catalog.browse import limit_to_radius
from catalog.generic import (
    GROUP_PAGE,
    SIZE_PARAMS,
    Facet,
    GenericFilters,
    GenericResult,
    GenericRow,
    apply_filters,
    facets,
    group_products,
    load_label_allowlist,
    match_offers,
    near_misses,
    parse_generic_filters,
)
from catalog.units import GENERIC_COMPARISON, display_generic_size
from normalize import load_store_brands
from normalize.text import fold

from .prefs import get_prefs, split_stores

HEADINGS = {
    "volume": "By volume",
    "mass": "By weight",
    "oz_labeled": "Per oz as labeled",
    "count": "By count",
    "unknown": "Size unknown",
}
PAGE_NOTE = "Matched by text and ranked by price per unit. For precise filters, pick a category."


FACET_TITLES = {
    "brand": "Brand",
    "brand_tier": "Brand type",
    "labels": "Labels",
    "path": "Browse by",
    "vol": "Volume",
    "mass": "Weight",
    "oz": "Per oz as labeled",
    "count": "Count",
}
SIZE_UNITS = {
    "vol": {"imperial": "fl oz", "metric": "L"},
    "mass": {"imperial": "oz", "metric": "kg"},
    "oz": {"imperial": "oz", "metric": "oz"},
    "count": {"imperial": "items", "metric": "items"},
}
FACET_PARAMS = {
    "brand": ("brand",),
    "brand_tier": ("brand_tier",),
    "labels": ("label",),
    "path": ("path",),
    **{key: (f"{key}_min", f"{key}_max") for key in SIZE_PARAMS.values()},
}
# Never carried in a facet URL: they describe one request, not the search.
TRANSIENT = ("q", "show", "sec", "poll")


def _query_string(get, *, drop: tuple[str, ...] = (), add: tuple[tuple[str, str], ...] = ()) -> str:
    pairs = [
        (k, v)
        for k, values in get.lists()
        for v in values
        if k not in TRANSIENT and k not in drop
    ]
    return urlencode([*pairs, *add])


def _facet_url(q: str, get, **kw) -> str:
    rest = _query_string(get, **kw)
    return f"{reverse('search')}?{urlencode({'q': q})}{'&' + rest if rest else ''}"


def _present_facet(facet: Facet, *, q: str, get, f: GenericFilters, unit_system: str) -> dict:
    params = FACET_PARAMS[facet.name]
    out = {
        "name": facet.name,
        "title": FACET_TITLES[facet.name],
        "active": facet.active,
        "clear_url": _facet_url(q, get, drop=params) if facet.active else None,
        "kind": "checks",
    }
    if facet.name in ("brand", "labels"):
        chosen = f.brands if facet.name == "brand" else f.labels
        out["param"] = params[0]
        out["options"] = [
            {"value": v, "label": label, "count": n, "checked": v in chosen}
            for v, label, n in facet.options
        ]
    elif facet.name == "brand_tier":
        out["kind"] = "select"
        out["param"] = "brand_tier"
        out["options"] = [
            {"value": v, "label": label, "count": n, "selected": v == f.brand_tier}
            for v, label, n in facet.options
        ]
    elif facet.name == "path":
        out["kind"] = "links"
        out["options"] = [
            {
                "label": label,
                "count": n,
                "url": _facet_url(q, get, drop=("path",), add=(("path", v),)),
            }
            for v, label, n in facet.options
        ]
    else:
        out["kind"] = "range"
        unit = SIZE_UNITS[facet.name][unit_system]
        out["unit"] = unit
        out["title"] = f"{FACET_TITLES[facet.name]} ({unit})"
        out["min_name"], out["max_name"] = params
        out["range_min"], out["range_max"] = facet.options[0][1], facet.options[1][1]
        out["current_min"] = get.get(params[0], "") if facet.active else ""
        out["current_max"] = get.get(params[1], "") if facet.active else ""
    return out


def status_text(total: int, *, fuzzy: bool, capped: bool, q: str, live: str | None) -> str:
    if fuzzy:
        text = f"Showing close matches for '{q}'"
    elif capped:
        text = "Showing the 500 closest matches; add words to narrow"
    else:
        text = f"{total} product{'' if total == 1 else 's'}"
    if live:
        text += f" · {live}"
    return text


def _present_row(row: GenericRow, *, observed: bool, unit_system: str, names: dict[str, str]) -> dict:
    from . import views  # views imports this module lazily; avoid an import cycle

    offer = row.offer
    adapted = SimpleNamespace(
        offer=offer, product=row.product, unit_price=None, unknown=(), reason=row.reason
    )
    dimension = offer.measure_dim if row.unit_price is not None else None
    comparison = GENERIC_COMPARISON.get(dimension or "", "")
    if row.unit_price is not None and comparison:
        adapted.unit_price = row.unit_price
    present = views._present_observed if observed else views._present
    out = present(adapted, comparison, unit_system, "")
    out["size"] = ""
    if offer.measure_dim in GENERIC_COMPARISON and offer.measure_amount is not None:
        out["size"] = display_generic_size(offer.measure_dim, offer.measure_amount, unit_system)
    slug = row.product.category.slug if row.product.category_id else None
    if slug in names:
        out["category_name"] = names[slug]
        out["category_url"] = reverse("category", args=[slug])
    return out


def _groups(result: GenericResult, *, section: str, observed: bool, unit_system: str, names, expand: str | None):
    entries = [(g.dimension, g.rows, g.total) for g in result.groups]
    if result.unknown:
        entries.append(("unknown", result.unknown, len(result.unknown)))
    groups = []
    for dimension, rows, total in entries:
        shown = rows if expand == dimension else rows[:GROUP_PAGE]
        groups.append(
            {
                "dimension": dimension,
                "section": section,
                "observed": observed,
                "dom_id": f"group-{section}-{dimension}",
                "heading": f"{HEADINGS[dimension]} ({total})",
                "total": total,
                "more": total > len(shown),
                "rows": [
                    _present_row(r, observed=observed, unit_system=unit_system, names=names) for r in shown
                ],
            }
        )
    return groups


def generic_results(request, q: str, *, definitions=None) -> HttpResponse:
    from . import views

    if definitions is None:
        definitions = views._active_definitions()
    names = {slug: d.name for slug, d in definitions.items()}
    prefs = get_prefs(request)
    when = views.now()
    live_stores, chain_stores = split_stores(prefs.stores)
    location, near = views._location(prefs)
    folded = fold(q)

    live = match_offers(folded, when, stores=live_stores)
    seen = match_offers(folded, when, observed=True, stores=chain_stores)
    seen_offers, hidden_far, hidden_unplaced = limit_to_radius(seen.offers, near)

    store_brands = load_store_brands(settings.STORE_BRANDS_FILE)
    allowlist = load_label_allowlist(settings.LABELS_FILE)
    filters = parse_generic_filters(request.GET, prefs.unit_system, allowlist)
    facet_list = facets([*live.offers, *seen_offers], filters, store_brands, allowlist, prefs.unit_system)
    live_offers = apply_filters(live.offers, filters, store_brands)
    seen_offers = apply_filters(seen_offers, filters, store_brands)

    live_result = group_products(live_offers)
    seen_result = group_products(seen_offers)
    unfiltered_hits = bool(live.offers or seen.offers or hidden_far or hidden_unplaced)
    no_results = not live_offers and not seen_offers and not hidden_far and not hidden_unplaced
    keep = _query_string(request.GET)
    held_total = len({o.product_id for o in [*live_offers, *seen_offers]})

    htmx = request.headers.get("HX-Request") == "true"
    restore = request.headers.get("HX-History-Restore-Request") == "true"
    show = request.GET.get("show", "")
    show_section = "observed" if request.GET.get("sec") == "observed" else "live"

    live_groups = _groups(
        live_result, section="live", observed=False, unit_system=prefs.unit_system, names=names,
        expand=show if show_section == "live" else None,
    )
    seen_groups = _groups(
        seen_result, section="observed", observed=True, unit_system=prefs.unit_system, names=names,
        expand=show if show_section == "observed" else None,
    )
    if show and htmx and not restore:
        pool = live_groups if show_section == "live" else seen_groups
        group = next((g for g in pool if g["dimension"] == show), None)
        if group is not None:
            response = render(request, "web/_generic_group.html", {"group": group})
            patch_vary_headers(response, ["HX-Request", "HX-History-Restore-Request", "Accept-Language"])
            return response

    misses = [
        {"name": m.name, "url": f"{reverse('category', args=[m.slug])}?q_rest={_quote(folded)}"}
        for m in near_misses(folded, [*live.offers, *seen.offers], definitions, names)
    ]
    context = {
        "q": q,
        "has_outbound": True,
        "has_amazon": any(
            r["offer"].retailer == "amazon" for g in live_groups for r in g["rows"]
        ),
        "page_note": PAGE_NOTE,
        "live_groups": live_groups,
        "observed_groups": seen_groups,
        "observed_total": seen_result.total_products,
        "observed_hidden_far": hidden_far,
        "observed_hidden_unplaced": hidden_unplaced,
        "location": location,
        "near_misses": misses,
        "empty": no_results and not unfiltered_hits,
        "filtered_empty": no_results and unfiltered_hits and filters.active,
        "facets": [
            _present_facet(x, q=q, get=request.GET, f=filters, unit_system=prefs.unit_system)
            for x in facet_list
        ],
        "filters_active": filters.active,
        "path_param": filters.path or "",
        "clear_all_url": _facet_url(q, request.GET, drop=tuple(p for ps in FACET_PARAMS.values() for p in ps)),
        "keep_qs": f"&{keep}" if keep else "",
        "live_empty": not live_groups and seen_result.total_products > 0,
        "status_text": status_text(
            held_total, fuzzy=live.fuzzy or seen.fuzzy, capped=live.capped or seen.capped, q=q, live=None
        ),
    }
    template = "web/_generic_results.html" if htmx and not restore else "web/generic.html"
    context["oob"] = template == "web/_generic_results.html"
    response = render(request, template, context)
    patch_vary_headers(response, ["HX-Request", "HX-History-Restore-Request", "Accept-Language"])
    return response


def _quote(text: str) -> str:
    from urllib.parse import quote_plus

    return quote_plus(text)
