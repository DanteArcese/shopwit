"""The generic results page: held products matched by text, grouped by measure."""

from __future__ import annotations

from types import SimpleNamespace

from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.cache import patch_vary_headers

from catalog.browse import limit_to_radius
from catalog.generic import GROUP_PAGE, GenericResult, GenericRow, group_products, match_offers, near_misses
from catalog.units import GENERIC_COMPARISON, display_generic_size
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

    live_result = group_products(live.offers)
    seen_result = group_products(seen_offers)

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

    total = live_result.total_products
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
        "empty": total == 0 and seen_result.total_products == 0 and not hidden_far and not hidden_unplaced,
        "status_text": status_text(
            total, fuzzy=live.fuzzy or seen.fuzzy, capped=live.capped or seen.capped, q=q, live=None
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
