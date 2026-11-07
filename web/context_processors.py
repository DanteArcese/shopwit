from catalog.models import Category

from .prefs import get_prefs


def prefs(request):
    return {
        "prefs": get_prefs(request),
        "active_categories": Category.objects.filter(active=True).order_by("name"),
    }
