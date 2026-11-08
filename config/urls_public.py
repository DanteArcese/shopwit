"""URLconf for the public apex host: nothing from the app is reachable here."""

from django.urls import include, path

from waitlist import views as waitlist_views
from web import health

urlpatterns = [
    path("", waitlist_views.landing, name="landing"),
    path("waitlist/", include("waitlist.urls")),
    path("privacy", waitlist_views.privacy, name="privacy"),
    path("healthz", health.healthz, name="healthz"),
    path("robots.txt", health.robots_txt, name="robots"),
]
