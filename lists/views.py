"""List pages: the visitor's lists, add-to-list, and the resolved list.

Every list id is looked up in the requesting visitor's own store, so an id that
belongs to anyone else is a 404 on every route (ids are not capabilities).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation

from django.core.exceptions import BadRequest
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from catalog.browse import browse, parse_filters_strict, spec_params
from catalog.generic import (
    GENERIC_TARGET_UNITS,
    generic_matching_offers,
    generic_target_to_canonical,
)
from catalog.models import Category, Offer, Product
from catalog.retailers import available_retailers, is_chain_code
from catalog.units import (
    TARGET_UNITS,
    default_target,
    target_to_canonical,
    target_units_for,
)
from normalize.generic import normalize_phrase
from web import views as web_views
from web.net import client_ip
from web.prefs import get_prefs, split_stores
from web.search import BOT_UA, FACET_PARAMS

from . import spec as sp
from .present import GENERIC_BY, dimension_of, item_label, present_list
from .resolve import MIXED, invalid_facets, queue_fetches, resolve_list
from .spec import ItemSpec, ListLimitError, ListSpec
from .store import store_for

AMOUNT_ERROR = "Enter an amount greater than 0"
ITEMS_FULL = f"This list is full ({sp.MAX_ITEMS} items)"
LISTS_FULL = f"You have the maximum of {sp.MAX_LISTS} lists"
LIST_GONE = "That list no longer exists"
SPLIT_CHOICES = {"1": 1, "2": 2, "3": 3, "all": None}
DEFAULT_SPLIT = "2"
# Form fields of the add page that are not part of the item's own spec.
RESERVED = frozenset(
    {
        "category",
        "amount",
        "unit",
        "list",
        "list_name",
        "csrfmiddlewaretoken",
        "kind",
        "q",
        "dim",
    }
)
FACET_KEYS = frozenset(p for params in FACET_PARAMS.values() for p in params)


def _now():
    return web_views.now()


def _owned(request, list_id: str) -> ListSpec:
    spec = store_for(request).get(list_id)
    if spec is None:
        raise Http404
    return spec


def _item(spec: ListSpec, item_id: str) -> ItemSpec:
    item = next((i for i in spec.items if i.id == item_id), None)
    if item is None:
        raise Http404
    return item


def _amount(raw: str | None) -> Decimal:
    try:
        return Decimal((raw or "").strip())
    except InvalidOperation as exc:
        raise ValueError(AMOUNT_ERROR) from exc


@never_cache
@require_GET
def index(request):
    lists = store_for(request).lists()
    return render(
        request, "lists/index.html", {"lists": [(s, len(s.items)) for s in lists]}
    )


@never_cache
@require_POST
def create(request):
    store = store_for(request)
    spec = sp.new_list(request.POST.get("name", ""), _now())
    try:
        store.save(spec)
    except ListLimitError:
        lists = store.lists()
        return render(
            request,
            "lists/index.html",
            {"lists": [(s, len(s.items)) for s in lists], "error": LISTS_FULL},
        )
    return redirect("lists:detail", list_id=spec.id)


# ---- add to list -----------------------------------------------------------------------


@dataclass
class Parsed:
    kind: str
    dimension: str
    label: str
    hidden: list[tuple[str, str]]
    category: Category | None = None
    filters: dict[str, str] = field(default_factory=dict)
    query: str = ""
    facets: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _parse_curated(data, unit_system: str) -> Parsed:
    category = (
        Category.objects.filter(slug=data.get("category", ""), active=True)
        .prefetch_related("attribute_defs")
        .first()
    )
    if category is None:
        raise Http404
    raw = {k: data[k] for k in data if k not in RESERVED and data[k] not in ("", "any")}
    spec, bad = parse_filters_strict(category, raw)
    if bad:
        raise BadRequest("invalid filter")
    filters = spec_params(spec)
    if len(filters) > sp.MAX_FILTERS or any(
        len(k) > sp.MAX_FILTER_VALUE or len(v) > sp.MAX_FILTER_VALUE
        for k, v in filters.items()
    ):
        raise BadRequest("filter too long")
    item = ItemSpec(
        id="-",
        category=category.slug,
        filters=filters,
        target_measure={
            "volume": "volume_ml",
            "mass": "mass_g",
            "count": "count",
            "paper": "rolls",
            "laundry": "loads",
            "tissue": "sheets",
        }[category.dimension],
        target_amount=Decimal(1),
    )
    return Parsed(
        kind="curated",
        dimension=category.dimension,
        label=item_label(item, category.name, unit_system, category.dimension),
        hidden=[("category", category.slug), *sorted(filters.items())],
        category=category,
        filters=filters,
    )


def _parse_generic(data, unit_system: str) -> Parsed:
    query = normalize_phrase(web_views.clean_query(data.get("q", "")))
    dimension = data.get("dim", "")
    if not query or len(query) > sp.MAX_QUERY or dimension not in sp.GENERIC_MEASURES:
        raise BadRequest("invalid generic item")
    facets: dict[str, tuple[str, ...]] = {}
    for key in sorted(FACET_KEYS):
        values = tuple(v for v in data.getlist(key) if v.strip())
        if values:
            facets[key] = values
    if len(facets) > sp.MAX_FILTERS or any(
        len(v) > sp.MAX_FACET_VALUES or any(len(x) > sp.MAX_FILTER_VALUE for x in v)
        for v in facets.values()
    ):
        raise BadRequest("facets too long")
    if invalid_facets(facets, unit_system):
        raise BadRequest("invalid facet")
    item = ItemSpec(
        id="-",
        category=None,
        filters={},
        target_measure=dimension,
        target_amount=Decimal(1),
        kind="generic",
        query=query,
        facets=facets,
        unit_system=unit_system,
    )
    return Parsed(
        kind="generic",
        dimension=dimension,
        label=item_label(item, None, unit_system),
        hidden=[
            ("kind", "generic"),
            ("q", query),
            ("dim", dimension),
            *((k, v) for k, vs in facets.items() for v in vs),
        ],
        query=query,
        facets=facets,
    )


def _parse(data, unit_system: str) -> Parsed:
    if data.get("kind") == "generic":
        return _parse_generic(data, unit_system)
    return _parse_curated(data, unit_system)


def _prefill(parsed: Parsed, prefs, now) -> tuple[str, str | None]:
    """(amount text, unit code) from the top match, else ("", None)."""
    stores = split_stores(prefs.stores)[0]
    if parsed.kind == "generic":
        offers = generic_matching_offers(
            parsed.query,
            {k: list(v) for k, v in parsed.facets.items()},
            parsed.dimension,
            stores,
            now,
            unit_system=prefs.unit_system,
        )
        offers = [
            o
            for o in offers
            if o.measure_amount is not None and o.generic_unit_price is not None
        ]
        if not offers:
            return "", None
        top = min(offers, key=lambda o: (o.generic_unit_price, o.pk))
        code = target_units_for(parsed.dimension, prefs.unit_system)[0]
        factor = GENERIC_TARGET_UNITS[parsed.dimension][code][1]
        amount = (Decimal(str(top.measure_amount)) / factor).quantize(Decimal("0.01"))
        return f"{amount.normalize():f}", code
    spec, _bad = parse_filters_strict(parsed.category, parsed.filters)
    result = browse(parsed.category, spec, None, stores, now, limit=1)
    rows = result.ranked or result.unranked
    if not rows:
        return "", None
    found = default_target(rows[0].offer.measures, parsed.dimension, prefs.unit_system)
    if found is None:
        return "", None
    amount, code = found
    return f"{amount.normalize():f}", code


def _valid_unit(kind: str, dimension: str, unit: str) -> bool:
    table = GENERIC_TARGET_UNITS if kind == "generic" else TARGET_UNITS
    return unit in table.get(dimension, {})


def _unit_options(parsed: Parsed, unit_system: str) -> list[tuple[str, str]]:
    codes = target_units_for(parsed.dimension, unit_system)
    if parsed.kind == "generic":
        return [(c, GENERIC_TARGET_UNITS[parsed.dimension][c][0]) for c in codes]
    return [(c, TARGET_UNITS[parsed.dimension][c][0]) for c in codes]


def _add_context(request, parsed: Parsed, prefs, **extra) -> dict:
    lists = store_for(request).lists()
    return {
        "summary": parsed.label,
        "hidden": parsed.hidden,
        "units": _unit_options(parsed, prefs.unit_system),
        "lists": lists,
        "kind_by": GENERIC_BY.get(parsed.dimension)
        if parsed.kind == "generic"
        else None,
        **extra,
    }


@never_cache
@require_http_methods(["GET", "POST"])
def add(request):
    prefs = get_prefs(request)
    data = request.POST if request.method == "POST" else request.GET
    parsed = _parse(data, prefs.unit_system)
    now = _now()
    store = store_for(request)
    if request.method == "GET":
        amount, unit = _prefill(parsed, prefs, now)
        lists = store.lists()
        context = _add_context(
            request,
            parsed,
            prefs,
            amount=amount,
            unit=unit,
            selected=lists[0].id if lists else "new",
        )
        return render(request, "lists/add.html", context)

    unit = data.get("unit", "")
    selected = data.get("list", "new")
    amount_text = data.get("amount", "")

    def again(error: str):
        context = _add_context(
            request,
            parsed,
            prefs,
            amount=amount_text,
            unit=unit,
            selected=selected,
            error=error,
        )
        return render(request, "lists/add.html", context)

    if not _valid_unit(parsed.kind, parsed.dimension, unit):
        raise BadRequest("invalid unit")
    try:
        amount = _amount(amount_text)
        if parsed.kind == "generic":
            measure, canonical = (
                parsed.dimension,
                generic_target_to_canonical(amount, unit, parsed.dimension),
            )
        else:
            measure, canonical = target_to_canonical(amount, unit, parsed.dimension)
    except ValueError:
        return again(AMOUNT_ERROR)

    if selected == "new":
        target = sp.new_list(data.get("list_name", ""), now)
    else:
        target = store.get(selected)
        if target is None:
            # A stale form (the list was deleted) or someone else's id: the same
            # answer for both, so it reveals nothing; ids are never capabilities.
            selected = "new"
            response = again(LIST_GONE)
            response.status_code = 404
            return response
    item = ItemSpec(
        id=sp.new_id(),
        category=parsed.category.slug if parsed.category else None,
        filters=parsed.filters,
        target_measure=measure,
        target_amount=canonical,
        kind=parsed.kind,
        query=parsed.query,
        facets=parsed.facets,
        unit_system=prefs.unit_system,
    )
    try:
        target = sp.with_item(target, item, now)
    except ListLimitError:
        return again(ITEMS_FULL)
    try:
        store.save(target)
    except ListLimitError:
        return again(LISTS_FULL)
    return redirect("lists:detail", list_id=target.id)


# ---- one list --------------------------------------------------------------------------


def _render_detail(
    request, spec: ListSpec, *, error=None, error_item=None, status=200, full_page=False
):
    prefs = get_prefs(request)
    now = _now()
    # A ?store= override counts only codes of live retailers with current prices;
    # nothing left (or no override) means the saved stores.
    asked = frozenset(request.GET.getlist("store"))
    override = (
        asked & {c for c in available_retailers(now) if not is_chain_code(c)}
        if asked
        else frozenset()
    )
    stores = override or prefs.stores
    split_key = request.GET.get("split", DEFAULT_SPLIT)
    if split_key not in SPLIT_CHOICES:
        split_key = DEFAULT_SPLIT
    if (
        full_page
        and request.method == "GET"
        and not BOT_UA.search(request.headers.get("User-Agent", ""))
    ):
        # The raw client IP, as search passes it: allow() salts and hashes the key.
        queue_fetches(spec, now, client_ip(request))
    resolution = resolve_list(spec, stores, now, SPLIT_CHOICES[split_key])
    presented = present_list(resolution, prefs.unit_system)
    context = {
        "list": spec,
        "view": presented,
        "oldest": resolution.oldest_price_at,
        "mixed": resolution.mixed_currency and MIXED,
        "split_key": split_key,
        "split_choices": list(SPLIT_CHOICES),
        "max_split": SPLIT_CHOICES[split_key],
        "store_params": [("store", s) for s in sorted(override)],
        "error": error,
        "error_item": error_item,
        "has_outbound": True,
        "has_amazon": presented["has_amazon"],
    }
    return render(request, "lists/detail.html", context, status=status)


@never_cache
@require_GET
def detail(request, list_id):
    spec = _owned(request, list_id)
    htmx = request.headers.get("HX-Request") == "true"
    return _render_detail(request, spec, full_page=not htmx)


@never_cache
@require_POST
def rename(request, list_id):
    spec = _owned(request, list_id)
    store_for(request).save(sp.renamed(spec, request.POST.get("name", ""), _now()))
    return redirect("lists:detail", list_id=list_id)


@never_cache
@require_http_methods(["GET", "POST"])
def delete(request, list_id):
    """GET asks to confirm; only a POST from that page (confirm=yes) deletes."""
    spec = _owned(request, list_id)
    if request.method == "GET":
        return render(request, "lists/delete.html", {"list": spec})
    if request.POST.get("confirm") != "yes":
        return redirect("lists:delete", list_id=list_id)
    store_for(request).delete(list_id)
    return redirect("lists:index")


@never_cache
@require_POST
def item_target(request, list_id, item_id):
    spec = _owned(request, list_id)
    item = _item(spec, item_id)
    unit = request.POST.get("unit", "")
    prefs = get_prefs(request)
    cat_dim = None
    if item.kind != "generic" and item.category:
        cat_dim = (
            Category.objects.filter(slug=item.category).values_list("dimension", flat=True).first()
        )
    dimension = dimension_of(item.target_measure, cat_dim)
    if not _valid_unit(item.kind, dimension, unit):
        raise BadRequest("invalid unit")
    try:
        amount = _amount(request.POST.get("amount"))
        if item.kind == "generic":
            measure, canonical = (
                item.target_measure,
                generic_target_to_canonical(amount, unit, dimension),
            )
        else:
            measure, canonical = target_to_canonical(amount, unit, dimension)
    except ValueError:
        return _render_detail(
            request, spec, error=AMOUNT_ERROR, error_item=item_id, status=200
        )
    updated = replace(item, target_measure=measure, target_amount=canonical)
    store_for(request).save(sp.replace_item(spec, updated, _now()))
    return redirect("lists:detail", list_id=list_id)


@never_cache
@require_POST
def item_remove(request, list_id, item_id):
    spec = _owned(request, list_id)
    _item(spec, item_id)
    store_for(request).save(sp.without_item(spec, item_id, _now()))
    return redirect("lists:detail", list_id=list_id)


@never_cache
@require_POST
def item_pin(request, list_id, item_id):
    spec = _owned(request, list_id)
    item = _item(spec, item_id)
    try:
        product_id = int(request.POST.get("product", ""))
    except (ValueError, OverflowError):
        raise BadRequest("invalid product") from None
    if item.kind == "generic":
        ok = Offer.objects.filter(
            product_id=product_id, measure_dim=item.target_measure, kind=Offer.Kind.LIVE
        ).exists()
    else:
        ok = Product.objects.filter(
            pk=product_id, category__slug=item.category
        ).exists()
    if not ok:
        raise BadRequest("product not in this item's category")
    store_for(request).save(
        sp.replace_item(spec, replace(item, pinned_product=product_id), _now())
    )
    return redirect("lists:detail", list_id=list_id)


@never_cache
@require_POST
def item_unpin(request, list_id, item_id):
    spec = _owned(request, list_id)
    item = _item(spec, item_id)
    store_for(request).save(
        sp.replace_item(spec, replace(item, pinned_product=None), _now())
    )
    return redirect("lists:detail", list_id=list_id)
