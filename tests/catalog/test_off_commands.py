import io

import httpx
import pytest
from django.core.management import call_command

from catalog.models import Category, OffEntry, Product
from enrichment import off

_real_default_client = off.default_client


@pytest.fixture
def sleeps(monkeypatch):
    calls = []
    monkeypatch.setattr(off, "default_sleep", calls.append)
    return calls


def install(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(off, "default_client", lambda: _real_default_client(transport=transport))


def ok(code="0093966010015", **product):
    return httpx.Response(200, json={"code": code, "status": 1, "product": {"code": code, **product}})


def category(slug):
    return Category.objects.create(slug=slug, name=slug, dimension="mass", comparison_default="per_oz")


def run(**kw):
    call_command("off_backfill", stdout=io.StringIO(), **kw)


@pytest.mark.django_db
def test_backfill_uses_opf_for_household(monkeypatch, sleeps):
    Product.objects.create(identity="tp", title="TP", upc="037000792468", category=category("household/toilet-paper"))
    Product.objects.create(identity="milk", title="Milk", upc="0093966010015", category=category("dairy/milk"))
    Product.objects.create(identity="nocat", title="X", upc="0811620020169")
    seen = []

    def handler(request):
        seen.append((request.url.host, request.url.path, request.url.params["fields"]))
        return ok(labels_tags=["en:x"], quantity="1", last_modified_t=1767225600)

    install(monkeypatch, handler)
    run()
    hosts = {path.rsplit("/", 1)[1]: host for host, path, _ in seen}
    assert hosts == {
        "0037000792468": "world.openproductsfacts.org",
        "0093966010015": "world.openfoodfacts.org",
        "0811620020169": "world.openfoodfacts.org",
    }
    assert all(f == "code,labels_tags,quantity,last_modified_t" for *_, f in seen)
    assert OffEntry.objects.get(pk="0037000792468").source == "opf"
    assert OffEntry.objects.get(pk="0093966010015").source == "off"
    assert sleeps == [1.0, 1.0]  # at most 1 request per second


@pytest.mark.django_db
def test_backfill_retries_5xx_then_records(monkeypatch, sleeps):
    Product.objects.create(identity="m", title="M", upc="0093966010015")
    responses = [httpx.Response(503), httpx.Response(502), ok(labels_tags=["en:organic"], quantity="1 gal")]

    install(monkeypatch, lambda request: responses.pop(0))
    run()
    assert responses == []
    entry = OffEntry.objects.get()
    assert entry.labels == ["en:organic"] and entry.quantity == "1 gal"
    assert sleeps == [1.0, 2.0]  # backoff


@pytest.mark.django_db
def test_backfill_gives_up_after_three_retries_without_recording(monkeypatch, sleeps):
    Product.objects.create(identity="m", title="M", upc="0093966010015")
    count = []

    def handler(request):
        count.append(1)
        return httpx.Response(503)

    install(monkeypatch, handler)
    run()
    assert len(count) == 4
    assert OffEntry.objects.count() == 0


@pytest.mark.django_db
def test_backfill_records_404_and_skips_next_time(monkeypatch, sleeps):
    Product.objects.create(identity="m", title="M", upc="0093966010015")
    count = []

    def handler(request):
        count.append(1)
        return httpx.Response(404, json={"status": 0})

    install(monkeypatch, handler)
    run()
    entry = OffEntry.objects.get()
    assert entry.labels == [] and entry.quantity is None and entry.last_modified is None
    assert entry.source == "off"
    run()
    assert len(count) == 1


@pytest.mark.django_db
def test_backfill_sends_user_agent(monkeypatch, sleeps):
    Product.objects.create(identity="m", title="M", upc="0093966010015")
    agents = []

    def handler(request):
        agents.append(request.headers["user-agent"])
        return ok()

    install(monkeypatch, handler)
    run()
    ua = "shopwit/0.1 (+https://github.com/DanteArcese/shopwit)"
    assert agents == [ua]
    with _real_default_client() as client:
        assert client.headers["user-agent"] == ua


@pytest.mark.django_db
def test_backfill_respects_limit_and_skips_cached(monkeypatch, sleeps):
    for i, upc in enumerate(["0093966010015", "0811620020169", "0078742148380"]):
        Product.objects.create(identity=str(i), title="P", upc=upc)
    OffEntry.objects.create(upc="0093966010015", source="off")
    count = []

    def handler(request):
        count.append(request.url.path)
        return ok()

    install(monkeypatch, handler)
    run(limit=1)
    assert len(count) == 1 and "0093966010015" not in count[0]


@pytest.mark.django_db
def test_backfill_does_not_retry_403(monkeypatch, sleeps):
    Product.objects.create(identity="m", title="M", upc="0093966010015")
    count = []

    def handler(request):
        count.append(1)
        return httpx.Response(403)

    install(monkeypatch, handler)
    run()
    assert len(count) == 1 and sleeps == []
    assert OffEntry.objects.count() == 0


@pytest.mark.django_db
def test_backfill_429_honors_retry_after(monkeypatch, sleeps):
    Product.objects.create(identity="m", title="M", upc="0093966010015")
    responses = [httpx.Response(429, headers={"Retry-After": "7"}), ok()]
    install(monkeypatch, lambda request: responses.pop(0))
    run()
    assert sleeps == [7.0]
    assert OffEntry.objects.count() == 1


@pytest.mark.django_db
def test_backfill_429_retry_after_is_bounded_and_defaults_to_backoff(monkeypatch, sleeps):
    Product.objects.create(identity="m", title="M", upc="0093966010015")
    responses = [httpx.Response(429, headers={"Retry-After": "3600"}), httpx.Response(429), ok()]
    install(monkeypatch, lambda request: responses.pop(0))
    run()
    assert sleeps == [60.0, 2.0]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "header, expected",
    [("0", 1.0), ("nan", 1.0), ("inf", 1.0), ("Wed, 21 Oct 2026 07:28:00 GMT", 1.0)],
)
def test_backfill_429_retry_after_floor_and_invalid_values(monkeypatch, sleeps, header, expected):
    Product.objects.create(identity="m", title="M", upc="0093966010015")
    responses = [httpx.Response(429, headers={"Retry-After": header}), ok()]
    install(monkeypatch, lambda request: responses.pop(0))
    run()
    assert sleeps == [expected]
    assert OffEntry.objects.count() == 1
