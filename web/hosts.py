from django.conf import settings
from django.http import HttpResponsePermanentRedirect


class HostRoutingMiddleware:
    """Serve the public site on PUBLIC_HOST and the full app everywhere else."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        public = settings.PUBLIC_HOST
        if not public:
            return self.get_response(request)
        host = request.get_host().split(":")[0].lower()
        if host == f"www.{public}":
            return HttpResponsePermanentRedirect(f"https://{public}{request.get_full_path()}")
        if host == public:
            request.urlconf = "config.urls_public"
        response = self.get_response(request)
        if settings.APP_HOST and host == settings.APP_HOST:
            response["X-Robots-Tag"] = "noindex, nofollow"
        return response
