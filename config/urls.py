"""URLconf for the beta host (behind Cloudflare Access): the public app plus the admin."""

from django.contrib import admin
from django.urls import path

from config import urls_public

urlpatterns = [path("admin/", admin.site.urls)] + urls_public.urlpatterns
