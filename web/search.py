"""The generic results page: held products matched by text, grouped by measure."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import urlencode

from django.conf import settings
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.cache import patch_vary_headers

from catalog.browse import limit_to_radius
from catalog.demand import record_demand
from catalog.fetch import enqueue
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
from catalog.models import FetchJob
from catalog.nutrition import RANKS, has_cost, parse_rank
from catalog.units import GENERIC_COMPARISON, display_generic_size
from connectors.registry import live_source_info
from normalize import load_store_brands
from normalize.generic import is_personal, normalize_phrase
from normalize.text import fold

from .net import client_ip
from .prefs import get_prefs, split_stores

logger = logging.getLogger(__name__)

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
TRANSIENT = ("q", "show", "sec", "poll", "seen")


POLL_WINDOW = 15  # seconds a page keeps polling for background results
# A poll/seen timestamp outside [now - POLL_MAX_AGE, now + POLL_MAX_AHEAD] is invalid: polling stops.
POLL_MAX_AGE = 3600
POLL_MAX_AHEAD = 60
SOURCE_NAMES = {"walmart": "Walmart", "off-prices": "Open Food Facts"}
# Obvious automated clients only. A missing User-Agent is not treated as a bot.
BOT_UA = re.compile(
    r"bot\b|bot/|crawl|spider|slurp|facebookexternalhit|curl/|wget|python-requests|httpx|"
    r"headlesschrome|lighthouse|monitor|preview",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class JobState:
    source: str
    status: str
    finished_at: datetime | None
    result_count: int


@dataclass(frozen=True)
class PollInfo:
    states: Sequence[JobState]
    polling: bool
    start: int
    seen: int


def should_poll(jobs: Sequence[JobState], start: float, now: float) -> bool:
    if start > now or now >= start + POLL_WINDOW:
        return False
    return any(j.status in FetchJob.ACTIVE for j in jobs)


def _names(jobs: Sequence[JobState]) -> str:
    return " and ".join(sorted({SOURCE_NAMES.get(j.source, j.source) for j in jobs}))


def live_parts(jobs: Sequence[JobState], polling: bool, enqueue_refused: bool) -> list[str]:
    """Source status messages, in display order."""
    parts: list[str] = []
    active = [j for j in jobs if j.status in FetchJob.ACTIVE]
    if active:
        names = _names(active)
        parts.append(
            f"checking {names}\u2026" if polling else f"Still checking {names}. Reload in a minute for more."
        )
    new = sum(j.result_count for j in jobs if j.status == FetchJob.Status.SUCCEEDED)
    if new > 0:
        parts.append(f"updated with {new} new product{'' if new == 1 else 's'}")
    parts += [
        f"{SOURCE_NAMES.get(j.source, j.source)} didn't respond; showing saved prices."
        for j in sorted(jobs, key=lambda j: j.source)
        if j.status == FetchJob.Status.FAILED
    ]
    if enqueue_refused:
        parts.append("Live search is busy; showing saved prices.")
    return parts


def live_status(jobs: Sequence[JobState], polling: bool, enqueue_refused: bool) -> str | None:
    return " \u00b7 ".join(live_parts(jobs, polling, enqueue_refused)) or None


def _load_states(phrase: str, since: datetime) -> list[JobState]:
    rows = (
        FetchJob.objects.filter(phrase=phrase)
        .filter(Q(status__in=FetchJob.ACTIVE) | Q(finished_at__gte=since))
        .order_by("source", "id")
        .values_list("source", "status", "finished_at", "result_count")
    )
    return [JobState(*row) for row in rows]


def _refused(phrase: str, sources, jobs, when: datetime) -> bool:
    """Some source neither has a job nor a fresh success: blocked, queue full or throttled."""
    queued = {j.source for j in jobs}
    for name, info in sources.items():
        if name in queued:
            continue
        fresh = FetchJob.objects.filter(
            source=name,
            phrase=phrase,
            status=FetchJob.Status.SUCCEEDED,
            finished_at__gte=when - info.refresh_after,
        ).exists()
        if not fresh:
            return True
    return False


def _as_state(job: FetchJob) -> JobState:
    return JobState(job.source, job.status, job.finished_at, job.result_count)


def _stamp(value: str | None, now: float) -> int | None:
    """A poll/seen timestamp within [now - POLL_MAX_AGE, now + POLL_MAX_AHEAD], else None."""
    try:
        stamp = int(value) if value is not None else None
    except ValueError:
        return None
    if stamp is None or not now - POLL_MAX_AGE <= stamp <= now + POLL_MAX_AHEAD:
        return None
    return stamp


def _poller_url(q: str, start: int, seen: int, rank: str = "unit") -> str:
    """The poll URL: ``q`` is URL-encoded, so it can never add a parameter. Facet values are
    read from the live form at poll time (``hx-include="#facets"``), so a filter change
    mid-poll is honored. ``rank`` is the validated rank the request asked for (never raw input),
    carried so a rank ignored for lack of costed offers still applies once they arrive. HTMX
    appends the included select after these URL params, so a rank chosen there wins."""
    pairs = [("q", q), ("poll", start), ("seen", seen)]
    if rank != "unit":
        pairs.append(("rank", rank))
    return f"{reverse('search')}?{urlencode(pairs)}"


def poll_results(request, q: str) -> HttpResponse:
    """An HTMX poll: the cheap poller alone while nothing changed, else the refreshed page parts."""
    from . import views

    when = views.now()
    now = when.timestamp()
    start, seen = _stamp(request.GET.get("poll"), now), _stamp(request.GET.get("seen"), now)
    valid = start is not None and seen is not None
    since = datetime.fromtimestamp(start if valid else now - POLL_WINDOW, UTC)
    states = _load_states(normalize_phrase(q), since)
    polling = valid and should_poll(states, start, now)
    if polling and not any(s.finished_at and s.finished_at.timestamp() > seen for s in states):
        url = _poller_url(q, start, seen, parse_rank(request.GET.get("rank")))
        response = render(request, "web/_poller.html", {"poller_url": url})
    else:
        response = generic_results(
            request, q, poll=PollInfo(states, polling, start if valid else 0, int(now))
        )
    response["Cache-Control"] = "no-store"
    patch_vary_headers(response, ["HX-Request", "HX-History-Restore-Request", "Accept-Language"])
    return response


def _query_string(get, *, drop: tuple[str, ...] = (), add: tuple[tuple[str, str], ...] = ()) -> str:
    pairs = [
        (k, v)
        for k, values in get.lists()
        for v in values
        if k not in TRANSIENT and k not in drop
    ]
    return urlencode([*pairs, *add])


def _with_rank(get, rank: str):
    """A copy of the query with ``rank`` replaced by the validated rank in use (dropped for unit),
    in its original place, so no raw ``rank`` value is ever echoed into a link."""
    out = get.copy()
    if rank == "unit":
        out.pop("rank", None)
    else:
        out.setlist("rank", [rank])
    return out


def _settings_next(q: str, get) -> str:
    """The search to come back to from Settings: q and facet values only (never poll/seen)."""
    keep = {p for ps in FACET_PARAMS.values() for p in ps}
    pairs = [(k, v) for k, values in get.lists() if k in keep for v in values]
    rank = parse_rank(get.get("rank"))
    if rank != "unit":
        pairs.append(("rank", rank))
    return f"{reverse('search')}?{urlencode([('q', q), *pairs])}"


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


def match_note(*, fuzzy: bool, capped: bool, q: str) -> str | None:
    if fuzzy:
        return f"Showing close matches for '{q}'"
    if capped:
        return "Showing the 500 closest matches; add words to narrow"
    return None


def status_text(total: int, *, fuzzy: bool, capped: bool, q: str, live: str | None) -> str:
    text = match_note(fuzzy=fuzzy, capped=capped, q=q) or f"{total} product{'' if total == 1 else 's'}"
    if live:
        text += f" · {live}"
    return text


def _present_row(
    row: GenericRow, *, observed: bool, unit_system: str, names: dict[str, str], rank: str = "unit"
) -> dict:
    from . import views  # views imports this module lazily; avoid an import cycle

    offer = row.offer
    adapted = SimpleNamespace(
        offer=offer, product=row.product, unit_price=None, unknown=(), reason=row.reason, cost=row.cost
    )
    dimension = offer.measure_dim if row.unit_price is not None else None
    comparison = GENERIC_COMPARISON.get(dimension or "", "")
    if row.unit_price is not None and comparison:
        adapted.unit_price = row.unit_price
    present = views._present_observed if observed else views._present
    out = present(adapted, comparison, unit_system, "", rank)
    out["size"] = ""
    if offer.measure_dim in GENERIC_COMPARISON and offer.measure_amount is not None:
        out["size"] = display_generic_size(offer.measure_dim, offer.measure_amount, unit_system)
    slug = row.product.category.slug if row.product.category_id else None
    if slug in names:
        out["category_name"] = names[slug]
        out["category_url"] = reverse("category", args=[slug])
    return out


def _add_url(q: str, dimension: str, get) -> str | None:
    """"Add to list" for a live measure group: the phrase, the dimension and the facets in use."""
    if dimension not in GENERIC_COMPARISON:
        return None
    keep = {p for ps in FACET_PARAMS.values() for p in ps}
    facet_pairs = [(k, v) for k, values in get.lists() if k in keep for v in values if v.strip()]
    return f"{reverse('lists:add')}?{urlencode([('kind', 'generic'), ('q', q), ('dim', dimension), *facet_pairs])}"


def _groups(
    result: GenericResult, *, section: str, observed: bool, unit_system: str, names, expand: str | None,
    q: str = "", get=None, rank: str = "unit",
):
    entries = [(g.dimension, g.rows, g.total, g.ranked_count) for g in result.groups]
    if result.unknown:  # never ranked by nutrition; listed as before, each with its reason
        entries.append(("unknown", result.unknown, len(result.unknown), len(result.unknown)))
    groups = []
    for dimension, rows, total, ranked_count in entries:
        shown = rows if expand == dimension else rows[:GROUP_PAGE]
        presented = [
            _present_row(r, observed=observed, unit_system=unit_system, names=names, rank=rank)
            for r in shown
        ]
        split = min(ranked_count, len(presented))
        groups.append(
            {
                "dimension": dimension,
                "section": section,
                "observed": observed,
                "dom_id": f"group-{section}-{dimension}",
                "add_url": None if observed or get is None else _add_url(q, dimension, get),
                "heading": f"{HEADINGS[dimension]} ({total})",
                "total": total,
                "more": total > len(shown),
                "rows": presented,
                "ranked_rows": presented[:split],
                "unranked_rows": presented[split:],
                "unranked_total": total - ranked_count,
            }
        )
    return groups


def generic_results(request, q: str, *, definitions=None, poll: PollInfo | None = None) -> HttpResponse:
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
    matched = [*live.offers, *seen_offers]  # in range, before any facet filter
    # Nutrition ranking is offered only when a matched offer has a protein or kcal cost;
    # otherwise ``rank`` is ignored, so a household search never ranks by nutrition.
    can_rank = any(has_cost(o) for o in matched)
    requested_rank = parse_rank(request.GET.get("rank"))
    rank = requested_rank if can_rank else "unit"
    get = _with_rank(request.GET, rank)  # URLs built from here carry only the rank in use
    filters = parse_generic_filters(request.GET, prefs.unit_system, allowlist)
    facet_list = facets(matched, filters, store_brands, allowlist, prefs.unit_system)
    live_offers = apply_filters(live.offers, filters, store_brands)
    seen_offers = apply_filters(seen_offers, filters, store_brands)

    live_result = group_products(live_offers, rank)
    seen_result = group_products(seen_offers, rank)
    unfiltered_hits = bool(live.offers or seen.offers or hidden_far or hidden_unplaced)
    no_results = not live_offers and not seen_offers and not hidden_far and not hidden_unplaced
    keep = _query_string(get)
    held_total = len({o.product_id for o in [*live_offers, *seen_offers]})

    htmx = request.headers.get("HX-Request") == "true"
    restore = request.headers.get("HX-History-Restore-Request") == "true"
    show = request.GET.get("show", "")
    show_section = "observed" if request.GET.get("sec") == "observed" else "live"

    live_groups = _groups(
        live_result, section="live", observed=False, unit_system=prefs.unit_system, names=names,
        expand=show if show_section == "live" else None, q=q, get=get, rank=rank,
    )
    seen_groups = _groups(
        seen_result, section="observed", observed=True, unit_system=prefs.unit_system, names=names,
        expand=show if show_section == "observed" else None, rank=rank,
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
    # Enqueue only on a full GET render by a non-bot: never HEAD, an HTMX request (facet change,
    # "Show all", history restore) or a poll, so a crawler or a repeat click cannot burn budgets.
    states: Sequence[JobState] = poll.states if poll else []
    polling = poll.polling if poll else False
    refused = False
    poller_url = _poller_url(q, poll.start, poll.seen, requested_rank) if poll and poll.polling else None
    if poll is not None:
        # A source refused on the first render (blocked, queue full, throttled) has no job to
        # report, so the busy note must be worked out again or it would vanish on the first poll.
        phrase = normalize_phrase(q)
        sources = live_source_info(live_off=settings.LIVE_OFF_ENABLED)
        if sources and phrase and not is_personal(phrase):
            refused = _refused(phrase, sources, states, when)
    elif (
        poll is None
        and not htmx
        and request.method == "GET"
        and not BOT_UA.search(request.headers.get("User-Agent", ""))
    ):
        phrase = normalize_phrase(q)
        try:
            record_demand(phrase, when.date())  # a genuine search: never a poll, swap or bot
        except Exception as exc:  # counting is best-effort; the type name only, never the phrase
            logger.error("search demand not recorded: %s", type(exc).__name__)
        sources = live_source_info(live_off=settings.LIVE_OFF_ENABLED)
        if sources and phrase and not is_personal(phrase):
            jobs = enqueue(phrase, "search", when, sources, client_key=client_ip(request))
            states = [_as_state(j) for j in jobs]
            refused = _refused(phrase, sources, jobs, when)
            if any(j.status in FetchJob.ACTIVE for j in jobs):
                polling = True
                start = int(when.timestamp())
                poller_url = _poller_url(q, start, start, requested_rank)
    parts = live_parts(states, polling, refused)
    live_text = " \u00b7 ".join(parts) or None
    fuzzy, capped = live.fuzzy or seen.fuzzy, live.capped or seen.capped
    note = match_note(fuzzy=fuzzy, capped=capped, q=q)
    finished = bool(states) and not any(s.status in FetchJob.ACTIVE for s in states)
    context = {
        "q": q,
        "poller_url": poller_url,
        "nothing_found": no_results
        and not unfiltered_hits
        and finished
        and any(s.status == FetchJob.Status.SUCCEEDED for s in states),
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
            _present_facet(x, q=q, get=get, f=filters, unit_system=prefs.unit_system)
            for x in facet_list
        ],
        "filters_active": filters.active,
        "path_param": filters.path or "",
        "clear_all_url": _facet_url(
            q, get, drop=tuple(p for ps in FACET_PARAMS.values() for p in ps)
        ),
        "rank": rank,
        "ranks": [{"value": v, "label": label} for v, label in RANKS.items()] if can_rank else [],
        "keep_qs": f"&{keep}" if keep else "",
        "live_empty": not live_groups and seen_result.total_products > 0,
        "status_text": status_text(held_total, fuzzy=fuzzy, capped=capped, q=q, live=live_text),
        # The same notes, visible: the status line above is a screen-reader live region only.
        "search_notes": [*([note] if note else []), *parts],
        "settings_next": _settings_next(q, get),
    }
    if poll is not None:
        template = "web/_generic_poll.html"
    else:
        template = "web/_generic_results.html" if htmx and not restore else "web/generic.html"
    context["oob"] = template == "web/_generic_results.html"
    response = render(request, template, context)
    patch_vary_headers(response, ["HX-Request", "HX-History-Restore-Request", "Accept-Language"])
    return response


def _quote(text: str) -> str:
    from urllib.parse import quote_plus

    return quote_plus(text)
