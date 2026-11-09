from django.conf import settings
from django.db import DatabaseError, connection
from django.http import HttpResponse, JsonResponse

from catalog.models import IngestionRun
from web.hosts import normalize_host


def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        newest = IngestionRun.objects.exclude(connector="openprices").order_by("-created_at").values_list("created_at", flat=True).first()
    except DatabaseError:
        response = JsonResponse({"status": "db_error"}, status=503)
    else:
        response = JsonResponse(
            {"status": "ok", "last_ingestion": newest.isoformat() if newest else None}
        )
    response["Cache-Control"] = "no-store"
    return response


# Pages that start live fetches or are per-visitor; crawlers must not spend budgets on them.
PUBLIC_DISALLOW = ("/search", "/settings", "/lists/", "/account/", "/waitlist/", "/click", "/feedback")


def robots_txt(request):
    host = normalize_host(request)
    if settings.PUBLIC_HOST and host == settings.PUBLIC_HOST:
        disallow = "".join(f"Disallow: {p}\n" for p in PUBLIC_DISALLOW)
        origin = (settings.PUBLIC_URL or f"https://{host}").rstrip("/")
        body = f"User-agent: *\nAllow: /\n{disallow}\nSitemap: {origin}/sitemap.xml\n"
    else:
        body = "User-agent: *\nDisallow: /\n"
    return HttpResponse(body, content_type="text/plain")


def placeholder(request):
    return HttpResponse("UnitPricer")
