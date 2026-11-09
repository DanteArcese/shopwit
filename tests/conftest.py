import pytest


@pytest.fixture(autouse=True)
def _no_access_middleware(request, settings):
    """Outside test_access.py, tests exercise routing and views, not Cloudflare Access.

    The Access middleware fails closed when APP_HOST is set without CF_ACCESS_AUD (as in
    most host tests), so it is removed here. A module opts back in with
    ``KEEP_ACCESS_MIDDLEWARE = True``.
    """
    if getattr(request.module, "KEEP_ACCESS_MIDDLEWARE", False):
        return
    settings.MIDDLEWARE = [m for m in settings.MIDDLEWARE if m != "web.access.CloudflareAccessMiddleware"]
