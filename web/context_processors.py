from catalog.models import Category

from .prefs import get_prefs
from .seo import public_origin


def prefs(request):
    return {
        "prefs": get_prefs(request),
        "public_origin": public_origin(request),
        "active_categories": Category.objects.filter(active=True).order_by("name"),
    }
