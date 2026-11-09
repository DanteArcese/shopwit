from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("", views.account, name="account"),
    path("sign-in", views.sign_in, name="sign_in"),
    path("magic-link", views.magic_request, name="magic_request"),
    path("magic/<str:token>", views.magic, name="magic"),
    path("sign-out", views.sign_out, name="sign_out"),
    path("password", views.password, name="password"),
    path("delete", views.delete, name="delete"),
]
