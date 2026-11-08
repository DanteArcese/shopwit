from django.contrib import admin
from django.urls import include, path

from web import health

urlpatterns = [
    path("healthz", health.healthz, name="healthz"),
    path("robots.txt", health.robots_txt, name="robots"),
    path("admin/", admin.site.urls),
    path("", include("web.urls")),
]
