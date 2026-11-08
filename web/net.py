from django.conf import settings


def client_ip(request) -> str:
    """CF-Connecting-IP when trusted (only cloudflared can reach us), else REMOTE_ADDR."""
    if settings.TRUST_CF_IP:
        forwarded = request.META.get("HTTP_CF_CONNECTING_IP", "").strip()
        if forwarded:
            return forwarded
    return request.META.get("REMOTE_ADDR", "")
