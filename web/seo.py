"""SEO helpers: the sitemap, the canonical origin and page descriptions."""

from django.conf import settings
from django.db.models import Max
from django.http import Http404, HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from catalog.freshness import fresh_offers
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


def category_description(name: str, unit_label: str, live_retailers: list[str]) -> str:
    unit = f" by price per {unit_label}" if unit_label else " by price per unit"
    sources = ", ".join(live_retailers) if live_retailers else "in-store prices"
    return f"Cheapest {name}{unit}, from {sources}."


def sitemap_xml(request):
    """Apex only: public pages and active curated categories, absolute URLs from PUBLIC_URL."""
    if settings.PUBLIC_HOST and normalize_host(request) != settings.PUBLIC_HOST:
        raise Http404
    origin = public_origin(request)
    newest = dict(
        fresh_offers(now())
        .filter(category__isnull=False)
        .values_list("category_id")
        .annotate(seen=Max("last_seen_at"))
    )
    entries = [(f"{origin}{reverse(name)}", None) for name in STATIC_PAGES]
    for cat in Category.objects.filter(active=True).order_by("slug"):
        seen = newest.get(cat.pk)
        entries.append((f"{origin}{reverse('category', args=[cat.slug])}", seen.date().isoformat() if seen else None))
    body = render_to_string("web/sitemap.xml", {"entries": entries})
    return HttpResponse(body, content_type="application/xml")
