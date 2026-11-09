import jwt
from django.conf import settings
from django.http import HttpResponseForbidden

from web.hosts import normalize_host

_jwks_client = None


def _get_jwks_client():
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(
            f"https://{settings.CF_ACCESS_TEAM_DOMAIN}/cdn-cgi/access/certs",
            cache_keys=True,
            lifespan=3600,
            timeout=5,
        )
    return _jwks_client


class CloudflareAccessMiddleware:
    """Reject non-public-host requests that lack a valid Cloudflare Access token."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.CF_ACCESS_AUD:
            return self.get_response(request)
        host = normalize_host(request)
        public = settings.PUBLIC_HOST
        # Fail closed: every host except the public ones needs a token. The apex is the
        # public app (no admin: config.urls_public); beta and any other host need Access.
        if (public and host in (public, f"www.{public}")) or (
            request.path == "/healthz"
            # In-container probe is plain HTTP; via the tunnel it carries this header.
            and "HTTP_X_FORWARDED_PROTO" not in request.META
        ):
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
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.PyJWTError:
            # Includes JWKS fetch failures: fail closed.
            return HttpResponseForbidden("Access required")
        request.access_email = claims.get("email", "")
        return self.get_response(request)
