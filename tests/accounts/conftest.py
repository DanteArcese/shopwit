import pytest
from django.core.cache import cache

from web import ratelimit

# A fixed instant well inside every rate-limit window (15 min, 1 h, 1 day): a test that
# ran across a window boundary would see its counter reset and flake.
_MID_WINDOW = 1_800_000_000 - 1_800_000_000 % 86_400 + 45_330


@pytest.fixture(autouse=True)
def _clear_cache(request):
    # The cache is the database cache; only tests with a database can clear it.
    if request.node.get_closest_marker("django_db"):
        request.getfixturevalue("db")
        cache.clear()


@pytest.fixture(autouse=True)
def _site(settings):
    settings.SITE_URL = "https://beta.unitpricer.test"
    settings.ALLOWED_HOSTS = ["testserver", "beta.unitpricer.com", "unitpricer.com", "evil.example"]
    settings.SECURE_SSL_REDIRECT = False


@pytest.fixture(autouse=True)
def _ratelimit_clock(monkeypatch):
    monkeypatch.setattr(ratelimit, "_time", lambda: float(_MID_WINDOW))
