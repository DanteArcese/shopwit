from django.contrib import admin
from django.urls import include, path

from feedback import views as feedback_views
from waitlist import views as waitlist_views
from web import health

urlpatterns = [
    path("healthz", health.healthz, name="healthz"),
    path("robots.txt", health.robots_txt, name="robots"),
    path("privacy", waitlist_views.privacy, name="privacy"),
    path("feedback", feedback_views.feedback, name="feedback"),
    path("admin/", admin.site.urls),
    path("lists/", include("lists.urls")),
    path("", include("web.urls")),
]
