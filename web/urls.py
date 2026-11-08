from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("c/<path:slug>/", views.category, name="category"),
    path("search", views.search, name="search"),
    path("settings", views.settings_page, name="settings"),
]
