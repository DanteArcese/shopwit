from decimal import Decimal

import httpx
import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache

from catalog.categories_sync import sync_categories
from catalog.models import Category, Offer, Product
from feedback import github
from tests.catalog.helpers import NOW

APP = "beta.unitpricer.com"
PUBLIC = "unitpricer.com"


@pytest.fixture(autouse=True)
def _env(settings):
    cache.clear()
    settings.PUBLIC_HOST = PUBLIC
    settings.APP_HOST = APP
    settings.ALLOWED_HOSTS = [PUBLIC, APP, "testserver"]
    settings.SECURE_SSL_REDIRECT = False
    settings.TRUST_CF_IP = True
    settings.GITHUB_TOKEN = ""
    settings.GITHUB_REPO = "owner/repo"


class FakeGitHub:
    """Records REST calls made through an httpx MockTransport."""

    def __init__(self):
        self.calls = []
        self.next_number = 100

    def handler(self, request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body, request.headers))
        if request.method == "POST" and request.url.path.endswith("/issues"):
            self.next_number += 1
            return httpx.Response(201, json={"number": self.next_number})
        if request.method == "PATCH":
            return httpx.Response(
                200, json={"number": int(request.url.path.rsplit("/", 1)[1])}
            )
        if request.method == "POST" and request.url.path.endswith("/comments"):
            return httpx.Response(201, json={"id": 1})
        return httpx.Response(404, json={})


@pytest.fixture(autouse=True)
def gh(monkeypatch):
    """Every test gets a mock transport: nothing can reach the network."""
    fake = FakeGitHub()
    monkeypatch.setattr(github, "_transport", httpx.MockTransport(fake.handler))
    return fake


@pytest.fixture
def token(settings):
    settings.GITHUB_TOKEN = "test-token"


@pytest.fixture
def offer(db):
    sync_categories(settings.CATEGORIES_DIR)
    milk = Category.objects.get(slug="dairy/milk")
    product = Product.objects.create(
        identity="x:milk", title="Acme 2% Milk", category=milk
    )
    return Offer.objects.create(
        product=product,
        retailer="walmart",
        retailer_item_id="1",
        price=Decimal("2.00"),
        currency="USD",
        url="https://example.com/1",
        category=milk,
        title="Acme 2% Milk, 1 L",
        last_seen_at=NOW,
    )


@pytest.fixture
def staff(client, db):
    user = get_user_model().objects.create_superuser("root", "r@example.com", "pw")
    client.force_login(user)
    return client
