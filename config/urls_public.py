"""URLconf for the public apex host: nothing from the app is reachable here."""

from django.urls import path

from web import health

urlpatterns = [
    path("", health.placeholder, name="landing"),
    path("privacy", health.placeholder, name="privacy"),
    path("healthz", health.healthz, name="healthz"),
    path("robots.txt", health.robots_txt, name="robots"),
]
