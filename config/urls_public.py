"""URLconf for the public apex host: the whole app except the admin.

The beta host (config/urls.py) serves these routes plus admin/. Add public routes here.
"""

from django.urls import include, path

from feedback import views as feedback_views
from waitlist import views as waitlist_views
from web import health

urlpatterns = [
    path("healthz", health.healthz, name="healthz"),
    path("robots.txt", health.robots_txt, name="robots"),
    path("privacy", waitlist_views.privacy, name="privacy"),
    path("feedback", feedback_views.feedback, name="feedback"),
    path("waitlist/", include("waitlist.urls")),
    path("lists/", include("lists.urls")),
    path("account/", include("accounts.urls")),
    path("", include("web.urls")),
]
