from __future__ import annotations

import re
from decimal import Decimal
from urllib.parse import urlencode

from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST

from catalog.browse import BRAND_TIERS, browse, parse_filters, spec_params
from catalog.coverage import nearby_categories, stores_we_cover
from catalog.freshness import fresh_observed
from catalog.geo import Near, locate, normalize_postal_code
from catalog.links import affiliate_programs
from catalog.models import Category, ClickEvent, Offer
from catalog.retailers import (
    RETAILERS,
    available_retailers,
    chain_names,
    display_name,
    is_chain_code,
)
from catalog.units import (
    DISPLAY,
    display_size,
    display_unit_price,
    format_money,
    size_to_canonical,
)
from connectors.registry import live_source_info
from normalize import load_categories
from normalize.query import resolve_query

from .prefs import (
    RADIUS_STEPS,
    Prefs,
    get_prefs,
    radius_choices,
    radius_km,
    radius_label,
    save_prefs,
    split_stores,
)
from .seo import category_description, category_has_prices

MAX_QUERY_LENGTH = 200
# dimension -> unit system -> (URL code written by the form, display label)
SIZE_UNITS = {
    "volume": {"imperial": ("floz", "fl oz"), "metric": ("l", "L")},
    "mass": {"imperial": ("oz", "oz"), "metric": ("kg", "kg")},
    "paper": {"imperial": ("sqft", "sq ft"), "metric": ("m2", "m²")},
    "count": {"imperial": ("ct", "ct"), "metric": ("ct", "ct")},
    "laundry": {"imperial": ("loads", "loads"), "metric": ("loads", "loads")},
    "tissue": {"imperial": ("sheets", "sheets"), "metric": ("sheets", "sheets")},
}
# Counted dimensions have one unit, so the URL code never picks a unit system.
COUNTED_DIMENSIONS = frozenset({"count", "laundry", "tissue"})


def _size_system(dimension: str, code: str | None, viewer_system: str) -> str:
    """The unit system a size bound is written in: the URL's size_unit when valid
    for this dimension, else the viewer's."""
    for system, (known, _label) in SIZE_UNITS.get(dimension, {}).items():
        if code == known and dimension not in COUNTED_DIMENSIONS:
            return system
    return viewer_system


def now():
    return timezone.now()


def home(request):
    when = now()
    prefs = get_prefs(request)  # reads the session only if a cookie came in; never creates one
    location, near = _location(prefs)
    nearby = [
        {"category": c, "count": n} for c, n in (nearby_categories(when, near) if near else [])
    ]
    response = render(
        request,
        "web/home.html",
        {
            "coverage": stores_we_cover(when),
            "coming_soon": settings.COMING_SOON,
            "location": location,
            "nearby": nearby,
        },
    )
    patch_vary_headers(response, ["Accept-Language"])
    return response


def safe_next(request, value: str | None) -> str:
    """A same-site relative path, else "/"."""
    if (
        value
        and value.startswith("/")
        and not value.startswith("//")
        and url_has_allowed_host_and_scheme(value, allowed_hosts={request.get_host()}, require_https=False)
    ):
        return value
    return "/"


def _has_safe_next(request, value: str | None) -> bool:
    """True when value was given and is a usable same-site path (not the "/" fallback)."""
    return bool(value) and (safe_next(request, value) == value)


def _store_options(prefs: Prefs, available: list[str]) -> list[dict]:
    """Available stores, plus saved ones that currently have no fresh offers."""
    chains = chain_names([*available, *(prefs.stores or ())])
    options = [{"code": c, "name": display_name(c, chains), "stale": False} for c in available]
    for code in sorted(prefs.stores or (), key=lambda c: (display_name(c, chains), c)):
        if code not in available:
            options.append(
                {"code": code, "name": f"{display_name(code, chains)} (no current prices)", "stale": True}
            )
    return options


def _canonical_params(category: Category, params, unit_system: str) -> dict[str, str]:
    """GET params with size_min/size_max converted from display units to canonical."""
    out = {k: params[k] for k in params}
    for key in ("size_min", "size_max"):
        raw = out.pop(key, None)
        if raw is None or not raw.strip():
            continue
        try:
            value = Decimal(raw.strip())
            if value.is_finite():
                out[key] = str(size_to_canonical(value, category.dimension, unit_system))
        except (ArithmeticError, ValueError):
            continue  # unparseable or out of range: ignore the bound
    return out


def _controls(category: Category, params) -> list[dict]:
    controls = []
    for attr in category.attribute_defs.order_by("name"):
        control = {
            "name": attr.name,
            "label": attr.name.replace("_", " ").capitalize(),
            "type": attr.type,
            "current": params.get(attr.name, ""),
        }
        if attr.type in ("enum", "int"):
            control["options"] = [str(v) for v in attr.values or []]
        controls.append(control)
    return controls


def _present(row, comparison: str, unit_system: str, dimension: str) -> dict:
    offer = row.offer
    unit_text = None
    price_text = format_money(offer.price, offer.currency)
    announce = f"price {price_text}"
    if row.unit_price is not None:
        price, label = display_unit_price(row.unit_price, comparison, unit_system)
        unit_text = f"{format_money(price, offer.currency)} / {label}"
        announce += f", {format_money(price, offer.currency)} per {label}"
    return {
        "price_label": announce,
        "title": offer.title or row.product.title,
        "brand": offer.brand or row.product.brand,
        "size": display_size(offer.measures, dimension, unit_system),
        "price": format_money(offer.price, offer.currency),
        "unit_price": unit_text,
        "retailer": "" if offer.kind == Offer.Kind.OBSERVED else display_name(offer.retailer),
        "offer": offer,
        "unknown": [f"{n.replace('_', ' ')} unknown" for n in row.unknown],
        "reason": row.reason,
    }


def _present_observed(row, comparison: str, unit_system: str, dimension: str) -> dict:
    out = _present(row, comparison, unit_system, dimension)
    offer = row.offer
    out.update(
        store=offer.store.display if offer.store else "a store",
        observed_on=offer.observed_on,
        is_sale=offer.is_sale,
        source_url=offer.url,
    )
    return out


def _location(prefs: Prefs) -> tuple[dict, Near | None]:
    """What to say about the saved postal code, and the filter it implies.

    state is "unset" (no code saved), "unknown" (not in the gazetteer: nothing
    is filtered) or "known".
    """
    info = {
        "postal_code": prefs.postal_code,
        "radius": radius_label(prefs.radius, prefs.unit_system),
    }
    if prefs.postal_code is None:
        return {**info, "state": "unset"}, None
    point = locate(prefs.postal_code, prefs.country)
    if point is None:
        return {**info, "state": "unknown"}, None
    return {**info, "state": "known"}, Near(*point, radius_km(prefs.radius, prefs.unit_system))


def _status_text(live: int, observed: int) -> str:
    if not observed:
        return f"{live} result{'' if live == 1 else 's'}"
    return (
        f"{live} online price{'' if live == 1 else 's'}, "
        f"{observed} in-store price{'' if observed == 1 else 's'}"
    )


def category(request, slug):
    cat = get_object_or_404(Category, slug=slug, active=True)
    prefs = get_prefs(request)
    when = now()
    params = request.GET
    size_system = _size_system(cat.dimension, params.get("size_unit"), prefs.unit_system)
    size_code, size_label = SIZE_UNITS.get(cat.dimension, {}).get(size_system, ("", ""))
    spec = parse_filters(cat, _canonical_params(cat, params, size_system))
    selected_stores = {s for s in params.getlist("store") if s}
    stores = frozenset(selected_stores) if selected_stores else prefs.stores
    live_stores, chain_stores = split_stores(stores)
    location, near = _location(prefs)
    # An explicit ?comparison= wins; else the saved default; browse() falls back to the category's.
    wanted = params.get("comparison") or prefs.comparisons.get(cat.slug)
    result = browse(cat, spec, wanted, live_stores, when)
    obs = browse(cat, spec, wanted, chain_stores, when, observed=True, near=near)

    url = reverse("category", args=[cat.slug])
    available = available_retailers(when, cat)
    has_prices = category_has_prices(cat, when)
    ranked = [_present(r, result.comparison, prefs.unit_system, cat.dimension) for r in result.ranked]
    unranked = [_present(r, result.comparison, prefs.unit_system, cat.dimension) for r in result.unranked]
    context = {
        # The banner stays on the full page even when a filter empties the list,
        # since HTMX swaps can bring links in later.
        "has_outbound": True,
        "has_amazon": any(r["offer"].retailer == "amazon" for r in ranked + unranked),
        "add_url": reverse("lists:add") + "?" + urlencode({"category": cat.slug, **spec_params(spec)}),
        "category": cat,
        "category_url": url,
        "coming_soon": settings.COMING_SOON,
        "controls": _controls(cat, params),
        "brand_tiers": BRAND_TIERS,
        "brand_tier": params.get("brand_tier", ""),
        "size_min": params.get("size_min", ""),
        "size_max": params.get("size_max", ""),
        "size_unit": size_label,
        "size_unit_code": size_code,
        "comparisons": [
            {"name": c, "label": DISPLAY.get((c, prefs.unit_system), ("", None))[0]}
            for c in [cat.comparison_default, *cat.comparison_alternates]
        ],
        "comparison": result.comparison,
        "stores": _store_options(prefs, available),
        "meta_description": category_description(
            cat.name,
            DISPLAY.get((cat.comparison_default, prefs.unit_system), ("", None))[0],
            [display_name(c) for c in available if not is_chain_code(c)],
            has_prices,
        ),
        # Search engines skip a category page while it has no prices to show.
        "noindex": not has_prices,
        "selected_stores": selected_stores or set(prefs.stores or ()),
        "ranked_total": result.total_products - result.unranked_total,
        "unranked_total": result.unranked_total,
        "status_total": result.total_products,
        "status_text": _status_text(result.total_products, obs.total_products),
        "filters_active": bool(
            spec.attributes or spec.brand_tier or live_stores
            or spec.size_min is not None or spec.size_max is not None
        ),
        "observed_filtered_empty": (
            chain_stores is not None
            and obs.total_products == 0
            and obs.hidden_far == 0
            and obs.hidden_unplaced == 0
            and fresh_observed(when).filter(product__category=cat, category=cat).exists()
        ),
        "location": location,
        "observed_hidden_far": obs.hidden_far,
        "observed_hidden_unplaced": obs.hidden_unplaced,
        "comparison_label": DISPLAY.get((result.comparison, prefs.unit_system), ("", None))[0],
        "ranked": ranked,
        "unranked": unranked,
        "observed_ranked": [_present_observed(r, obs.comparison, prefs.unit_system, cat.dimension) for r in obs.ranked],
        "observed_unranked": [_present_observed(r, obs.comparison, prefs.unit_system, cat.dimension) for r in obs.unranked],
        "observed_total": obs.total_products,
        "observed_ranked_total": obs.total_products - obs.unranked_total,
        "observed_unranked_total": obs.unranked_total,
    }
    q_rest = params.get("q_rest", "").strip()
    if q_rest:
        rest = [(k, v) for k in params for v in params.getlist(k) if k != "q_rest"]
        context["q_rest"] = q_rest
        context["dismiss_url"] = url + (f"?{urlencode(rest)}" if rest else "")
    htmx = request.headers.get("HX-Request") == "true"
    # A history-restore cache miss is also an HX-Request but needs the full page.
    restore = request.headers.get("HX-History-Restore-Request") == "true"
    template = "web/_results.html" if htmx and not restore else "web/category.html"
    context["oob"] = template == "web/_results.html"
    response = render(request, template, context)
    patch_vary_headers(response, ["HX-Request", "HX-History-Restore-Request", "Accept-Language"])
    return response


def _active_definitions():
    active = set(Category.objects.filter(active=True).values_list("slug", flat=True))
    return {s: d for s, d in load_categories(settings.CATEGORIES_DIR).items() if s in active}


def _redirect_query(resolution) -> str:
    pairs = [
        (name, "true" if value is True else str(value))
        for name, value in sorted(resolution.filters.items())
    ]
    if resolution.leftover:
        pairs.append(("q_rest", resolution.leftover))
    return urlencode(pairs)


# NUL breaks Postgres text parameters (a 500); other C0 controls and DEL are never search words.
_NUL = re.compile("\x00")
_CONTROLS = re.compile("[\x01-\x1f\x7f]")


def clean_query(raw: str) -> str:
    """The query with NUL removed, other control characters as spaces, trimmed and capped."""
    q = _CONTROLS.sub(" ", _NUL.sub("", raw))
    return q.strip()[:MAX_QUERY_LENGTH].strip()


def search(request):
    q = clean_query(request.GET.get("q", ""))
    if not q:
        return redirect("home")
    if request.headers.get("HX-Request") == "true" and "poll" in request.GET:
        from .search import poll_results  # a poll answers before any category lookup

        return poll_results(request, q)
    definitions = _active_definitions()
    resolution = resolve_query(q, definitions)
    if resolution.category is not None:
        target = reverse("category", args=[resolution.category])
        query = _redirect_query(resolution)
        return HttpResponseRedirect(f"{target}?{query}" if query else target)

    from .search import (
        generic_results,  # imported here: web.search uses helpers from this module
    )

    return generic_results(request, q, definitions=definitions)


_LIVE_SOURCE_LABELS = {"walmart": "Walmart", "off-prices": "Open Food Facts"}


def _join(items: list[str]) -> str:
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


def disclosure(request):
    programs = affiliate_programs(settings.AFFILIATE)
    live = live_source_info(live_off=settings.LIVE_OFF_ENABLED)
    live_names = [label for key, label in _LIVE_SOURCE_LABELS.items() if key in live]
    return render(
        request,
        "web/disclosure.html",
        {
            "observed_days": settings.OBSERVED_MAX_AGE.days,
            "programs": _join(programs),
            "has_programs": bool(programs),
            "amazon_tag": bool(settings.AFFILIATE.get("amazon_associates_tag")),
            "live_names": _join(live_names),
        },
    )


def terms(request):
    return render(request, "web/terms.html")


def about(request):
    return render(request, "web/about.html")


@csrf_exempt
@require_POST
def click(request):
    """Count an outbound click: offer id + retailer + time, nothing else."""
    try:
        offer = Offer.objects.filter(pk=int(request.GET.get("o", ""))).first()
    except (ValueError, OverflowError):
        offer = None
    if offer is not None and offer.kind != Offer.Kind.LIVE:
        raise Http404  # observed prices have no outbound link and are never counted
    if offer is not None:
        ClickEvent.objects.create(offer=offer, retailer=offer.retailer)
    return HttpResponse(status=204)


def _compare_categories() -> list[Category]:
    return list(Category.objects.filter(active=True).order_by("slug"))


def _posted_comparisons(post, prefs: Prefs) -> dict[str, str]:
    """Saved comparisons after a settings POST. Only active categories and their own
    comparisons survive; "Default" removes the key; a field not posted keeps the old one."""
    out: dict[str, str] = {}
    for cat in _compare_categories():
        allowed = [cat.comparison_default, *cat.comparison_alternates]
        value = post.get(f"cmp:{cat.slug}", prefs.comparisons.get(cat.slug, ""))
        if value in allowed and value != cat.comparison_default:
            out[cat.slug] = value
    return out


POSTAL_CODE_ERROR = "Enter a 5-digit US ZIP code. Your other changes were saved."


@sensitive_post_parameters("postal_code")
@require_http_methods(["GET", "POST"])
def settings_page(request):
    prefs = get_prefs(request)
    when = now()
    codes = available_retailers(when)
    postal_error = None
    next_url = safe_next(request, request.GET.get("next"))
    if request.method == "POST":
        system = request.POST.get("unit_system")
        if system not in ("imperial", "metric"):
            system = prefs.unit_system
        known = set(RETAILERS) | set(codes) | set(prefs.stores or ())
        chosen = frozenset(request.POST.getlist("stores")) & known
        if request.POST.get("all_stores"):
            stores = None
        else:
            stores = chosen or prefs.stores  # no/invalid selection keeps the previous one
        postal_code = prefs.postal_code
        if "postal_code" in request.POST:  # absent: keep; blank: clear; malformed: keep + error
            raw = request.POST["postal_code"].strip()
            if not raw:
                postal_code = None
            elif (normalized := normalize_postal_code(raw)) is not None:
                postal_code = normalized
            else:
                postal_error = POSTAL_CODE_ERROR
        try:
            radius = int(request.POST.get("radius", ""))
        except ValueError:
            radius = prefs.radius
        if radius not in RADIUS_STEPS:
            radius = prefs.radius
        prefs = Prefs(
            system, stores, postal_code=postal_code, radius=radius,
            comparisons=_posted_comparisons(request.POST, prefs),
        )
        save_prefs(request, prefs)
        next_url = safe_next(request, request.POST.get("next"))
        if postal_error is None:
            # POST/redirect/GET back to Settings, keeping a safe next for the back link.
            params = {"saved": "1"}
            if _has_safe_next(request, request.POST.get("next")):
                params["next"] = next_url
            return HttpResponseRedirect(f"{reverse('settings')}?{urlencode(params)}")
    # The form always shows what is saved (never a rejected postal code).
    location, _near = _location(prefs)
    return render(
        request,
        "web/settings.html",
        {
            "next": next_url,
            "saved": request.method == "GET" and request.GET.get("saved") == "1",
            "back_url": (
                next_url
                if _has_safe_next(request, request.GET.get("next") if request.method == "GET" else request.POST.get("next"))
                and next_url.split("?")[0].split("#")[0].rstrip("/") != reverse("settings")
                else None
            ),
            "stores": _store_options(prefs, codes),
            "location": location,
            "postal_value": prefs.postal_code or "",
            "postal_error": postal_error,
            "radius_choices": radius_choices(prefs.unit_system),
            "compare_rows": [
                {
                    "slug": cat.slug,
                    "name": cat.name,
                    "default_label": DISPLAY.get((cat.comparison_default, prefs.unit_system), ("", None))[0],
                    "saved": prefs.comparisons.get(cat.slug, ""),
                    "choices": [
                        {"name": c, "label": DISPLAY.get((c, prefs.unit_system), ("", None))[0]}
                        for c in cat.comparison_alternates
                    ],
                }
                for cat in _compare_categories()
            ],
        },
    )
