import pytest
from django.core.cache import cache


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
