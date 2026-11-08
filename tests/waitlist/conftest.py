import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


@pytest.fixture(autouse=True)
def _public_url(settings):
    settings.PUBLIC_URL = "https://unitpricer.com"
