from .prefs import get_prefs
from .seo import public_origin


def prefs(request):
    return {
        "prefs": get_prefs(request),
        "public_origin": public_origin(request),
    }
