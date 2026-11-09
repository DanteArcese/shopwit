"""SEO helpers: the sitemap, the canonical origin and page descriptions."""

from datetime import date

from django.conf import settings
from django.db.models import Max
from django.http import Http404, HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from catalog.freshness import fresh_observed, fresh_offers
from catalog.models import Category

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
