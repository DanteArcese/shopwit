from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("c/<path:slug>/", views.category, name="category"),
    path("search", views.search, name="search"),
    path("click", views.click, name="click"),
    path("disclosure", views.disclosure, name="disclosure"),
    path("terms", views.terms, name="terms"),
    path("about", views.about, name="about"),
    path("settings", views.settings_page, name="settings"),
]
