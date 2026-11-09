from django.conf import settings
from django.http import HttpResponsePermanentRedirect


def normalize_host(request):
    """Host without port or trailing dot, lowercased (Django accepts 'host.' in ALLOWED_HOSTS)."""
    return request.get_host().split(":")[0].rstrip(".").lower()


class HostRoutingMiddleware:
    """Serve the public app on PUBLIC_HOST and the app plus admin elsewhere (noindex on every other host)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        public = settings.PUBLIC_HOST
        if not public:
            return self.get_response(request)
        host = normalize_host(request)
        if host == f"www.{public}":
            return HttpResponsePermanentRedirect(f"https://{public}{request.get_full_path()}")
        if host == public:
            request.urlconf = "config.urls_public"
        response = self.get_response(request)
        if host != public:
            # Beta and any other allowed host (127.0.0.1, the container name) stay out of indexes.
            response["X-Robots-Tag"] = "noindex, nofollow"
        return response
