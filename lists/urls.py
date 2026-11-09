from django.urls import path, register_converter

from . import views

app_name = "lists"


class UidConverter:
    regex = "[0-9a-f]{32}"

    def to_python(self, value: str) -> str:
        return value

    def to_url(self, value: str) -> str:
        return value


register_converter(UidConverter, "uid")

urlpatterns = [
    path("", views.index, name="index"),
    path("new", views.create, name="create"),
    path("add", views.add, name="add"),
    path("<uid:list_id>/", views.detail, name="detail"),
    path("<uid:list_id>/rename", views.rename, name="rename"),
    path("<uid:list_id>/delete", views.delete, name="delete"),
    path(
        "<uid:list_id>/items/<uid:item_id>/target",
        views.item_target,
        name="item_target",
    ),
    path(
        "<uid:list_id>/items/<uid:item_id>/remove",
        views.item_remove,
        name="item_remove",
    ),
    path("<uid:list_id>/items/<uid:item_id>/pin", views.item_pin, name="item_pin"),
    path(
        "<uid:list_id>/items/<uid:item_id>/unpin", views.item_unpin, name="item_unpin"
    ),
]
