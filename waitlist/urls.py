from django.urls import path

from . import views

app_name = "waitlist"

urlpatterns = [
    path("", views.signup, name="signup"),
    path("confirm/<str:token>", views.confirm, name="confirm"),
    path("unsubscribe/<str:token>", views.unsubscribe, name="unsubscribe"),
]
