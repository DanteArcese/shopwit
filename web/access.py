import jwt
from django.conf import settings
from django.http import HttpResponseForbidden

_jwks_client = None


def _get_jwks_client():
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(
            f"https://{settings.CF_ACCESS_TEAM_DOMAIN}/cdn-cgi/access/certs",
            cache_keys=True,
            lifespan=3600,
        )
    return _jwks_client


class CloudflareAccessMiddleware:
    """Reject app-host requests that lack a valid Cloudflare Access token."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.CF_ACCESS_AUD or not settings.APP_HOST:
            return self.get_response(request)
        host = request.get_host().split(":")[0].lower()
        if host != settings.APP_HOST or request.path == "/healthz":
            return self.get_response(request)
        token = request.META.get("HTTP_CF_ACCESS_JWT_ASSERTION")
        if not token:
            return HttpResponseForbidden("Access required")
        try:
            key = _get_jwks_client().get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=settings.CF_ACCESS_AUD,
                issuer=f"https://{settings.CF_ACCESS_TEAM_DOMAIN}",
            )
        except jwt.PyJWTError:
            # Includes JWKS fetch failures: fail closed.
            return HttpResponseForbidden("Access required")
        request.access_email = claims.get("email", "")
        return self.get_response(request)
