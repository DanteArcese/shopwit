"""SEO helpers: the sitemap, the canonical origin and page descriptions."""

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db.models import Max
from django.http import Http404, HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from catalog.freshness import fresh_observed, fresh_offers
from catalog.models import Category
from catalog.units import DISPLAY, format_money

from .hosts import normalize_host


def now():
    return timezone.now()


STATIC_PAGES = ("home", "about", "privacy", "terms", "disclosure")


def public_origin(request) -> str:
    """https://unitpricer.com from PUBLIC_URL, else https://{PUBLIC_HOST}; the request's own
    origin only when both are unset (dev)."""
    if settings.PUBLIC_URL:
        return settings.PUBLIC_URL.rstrip("/")
    if settings.PUBLIC_HOST:
        return f"https://{settings.PUBLIC_HOST}"
    return request.build_absolute_uri("/").rstrip("/")


def category_description(
    name: str, unit_label: str, live_retailers: list[str], has_prices: bool = True
) -> str:
    unit = f" by price per {unit_label}" if unit_label else " by price per unit"
    if not has_prices:
        return f"No prices yet for {name}. UnitPricer will compare it{unit} once stores report prices."
    sources = ", ".join(live_retailers) if live_retailers else "in-store prices"
    return f"Cheapest {name}{unit}, from {sources}."


# Readable long forms of the unit labels in catalog.units.DISPLAY. Count-like labels
# ("roll", "load", "100 sheets", ...) read fine as they are; "each" reads as "item".
_UNIT_WORDS = {"oz": "ounce", "fl oz": "fluid ounce", "kg": "kg", "L": "litre", "each": "item"}
_KNOWN_LABELS = frozenset(label for label, _ in DISPLAY.values())


def unit_word(label: str) -> str:
    """The word for a unit label in prose ("oz" -> "ounce"); "" for a blank or unknown label."""
    if label not in _KNOWN_LABELS:
        return ""
    return _UNIT_WORDS.get(label, label)


def category_title(name: str, unit_label: str) -> str:
    word = unit_word(unit_label)
    if not word:
        return f"{name}: compare price per unit | UnitPricer"
    return f"{name} price per {word}: compare stores | UnitPricer"


def option_label(value) -> str:
    """A filter option's human label: "long_grain" -> "Long grain". The submitted value stays raw."""
    text = str(value).replace("_", " ")
    return text[:1].upper() + text[1:]


def format_day(day: date) -> str:
    return f"{day:%b} {day.day}, {day.year}"


@dataclass(frozen=True)
class PriceFact:
    """One shown product's best offer: its unit price in display units (None when it cannot
    be ranked), the store it was seen at and the day it was seen."""

    price: Decimal | None
    currency: str
    store: str
    seen: date


@dataclass(frozen=True)
class PriceSummary:
    count: int
    stores: int
    lowest: PriceFact | None
    median: Decimal | None  # None below 3 priced rows
    latest: date


def median_decimal(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def summarize_prices(facts: list[PriceFact]) -> PriceSummary | None:
    if not facts:
        return None
    priced = [f for f in facts if f.price is not None]
    return PriceSummary(
        count=len(facts),
        stores=len({f.store for f in facts}),
        lowest=min(priced, key=lambda f: f.price) if priced else None,
        median=median_decimal([f.price for f in priced]) if len(priced) >= 3 else None,
        latest=max(f.seen for f in facts),
    )


def plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def category_prices_description(unit_label: str, summary: PriceSummary) -> str:
    word = unit_word(unit_label) or "unit"
    text = f"{plural(summary.count, 'price')} from {plural(summary.stores, 'store')}."
    if summary.lowest is not None:
        low = summary.lowest
        text += (
            f" Lowest seen: {format_money(low.price, low.currency)}{'/' + unit_label if unit_label else ''} "
            f"at {low.store} ({format_day(low.seen)})."
        )
    return f"{text} Compared by price per {word}."


def priced_categories(when) -> dict[int, date]:
    """Category id -> newest price date, for categories with a fresh live or observed offer."""
    live = (
        fresh_offers(when)
        .filter(category__isnull=False)
        .values_list("category_id")
        .annotate(seen=Max("last_seen_at"))
    )
    observed = (
        fresh_observed(when)
        .filter(category__isnull=False)
        .values_list("category_id")
        .annotate(seen=Max("observed_on"))
    )
    days = [(pk, seen.date()) for pk, seen in live] + list(observed)
    newest: dict[int, date] = {}
    for pk, day in days:
        newest[pk] = max(newest.get(pk, day), day)
    return newest


def category_has_prices(category, when) -> bool:
    return (
        fresh_offers(when).filter(category=category).exists()
        or fresh_observed(when).filter(category=category).exists()
    )


def sitemap_xml(request):
    """Apex only: public pages and active curated categories with fresh prices, absolute URLs
    from PUBLIC_URL. A category with nothing to show yet stays out (its page is noindex)."""
    if settings.PUBLIC_HOST and normalize_host(request) != settings.PUBLIC_HOST:
        raise Http404
    origin = public_origin(request)
    newest = priced_categories(now())
    entries = [(f"{origin}{reverse(name)}", None) for name in STATIC_PAGES]
    for cat in Category.objects.filter(active=True, pk__in=list(newest)).order_by("slug"):
        entries.append((f"{origin}{reverse('category', args=[cat.slug])}", newest[cat.pk].isoformat()))
    body = render_to_string("web/sitemap.xml", {"entries": entries})
    return HttpResponse(body, content_type="application/xml")


def jsonld(data) -> str:
    """JSON for a <script type="application/ld+json"> block; "<" is escaped so no value can
    close the script element."""
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")


def website_jsonld(origin: str) -> str:
    return jsonld({
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": "UnitPricer",
        "url": f"{origin}/",
        "potentialAction": {
            "@type": "SearchAction",
            "target": {"@type": "EntryPoint", "urlTemplate": f"{origin}/search?q={{search_term_string}}"},
            "query-input": "required name=search_term_string",
        },
    })


def breadcrumb_jsonld(origin: str, name: str, path: str) -> str:
    return jsonld({
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "UnitPricer", "item": f"{origin}/"},
            {"@type": "ListItem", "position": 2, "name": name, "item": f"{origin}{path}"},
        ],
    })
