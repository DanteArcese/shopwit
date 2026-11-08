from django.conf import settings
from django.db import DatabaseError, connection
from django.http import HttpResponse, JsonResponse

from catalog.models import IngestionRun


def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        newest = IngestionRun.objects.order_by("-created_at").values_list("created_at", flat=True).first()
    except DatabaseError:
        response = JsonResponse({"status": "db_error"}, status=503)
    else:
        response = JsonResponse(
            {"status": "ok", "last_ingestion": newest.isoformat() if newest else None}
        )
    response["Cache-Control"] = "no-store"
    return response


def robots_txt(request):
    host = request.get_host().split(":")[0].lower()
    if settings.PUBLIC_HOST and host == settings.PUBLIC_HOST:
        body = "User-agent: *\nAllow: /\n"
    else:
        body = "User-agent: *\nDisallow: /\n"
    return HttpResponse(body, content_type="text/plain")


def placeholder(request):
    return HttpResponse("UnitPricer")
