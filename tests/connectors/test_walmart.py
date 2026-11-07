import base64
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from connectors import registry
from connectors.base import ConnectorError
from connectors.recording import recorded_transport
from connectors.walmart import WalmartConnector, sign

RECORDINGS = Path(__file__).parent / "recordings" / "walmart"
CONSUMER = "consumer-abc-123"


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _der_b64(key):
    der = key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return base64.b64encode(der).decode()


def _pem(key):
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _connector(key, **kw):
    seen = []

    def spy(request):
        seen.append(request)
        return inner(request)

    inner = recorded_transport(RECORDINGS).handler
    client = httpx.Client(transport=httpx.MockTransport(spy))
    c = WalmartConnector(
        CONSUMER, _der_b64(key), "7", client=client, clock=lambda: 1700000000.123, **kw
    )
    return c, seen


def test_signature_verifies_with_public_key(key):
    sig = sign(CONSUMER, "1700000000123", "7", key)
    key.public_key().verify(
        base64.b64decode(sig),
        f"{CONSUMER}\n1700000000123\n7\n".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


def test_search_maps_items_to_raw_offers(key):
    c, seen = _connector(key, publisher_id="PUB9")
    result = c.search("milk")
    assert result.skipped == 0 and len(result.offers) == 3
    o = result.offers[0]
    assert o.retailer == "walmart"
    assert o.retailer_item_id == "10315"
    assert o.title == "Great Value 2% Reduced Fat Milk, 1 Gallon"
    assert o.price == Decimal("2.98")
    assert o.currency == "USD"
    assert o.url == "https://www.walmart.com/ip/10315"
    assert "goto.walmart.com" not in o.url
    assert o.upc == "007874201001"
    assert o.brand == "Great Value"
    assert o.size_text == "1 gal"
    assert (
        o.fetched_at.tzinfo is UTC
        and abs((datetime.now(UTC) - o.fetched_at).total_seconds()) < 60
    )
    assert (
        result.offers[1].size_text is None
        and result.offers[1].upc is None
        and result.offers[1].brand is None
    )
    req = seen[0]
    assert req.url.params["publisherId"] == "PUB9"
    assert req.url.params["numItems"] == "25"
    assert req.url.params["responseGroup"] == "full"
    assert req.headers["WM_CONSUMER.ID"] == CONSUMER
    assert req.headers["WM_CONSUMER.INTIMESTAMP"] == "1700000000123"
    assert req.headers["WM_SEC.KEY_VERSION"] == "7"
    key.public_key().verify(
        base64.b64decode(req.headers["WM_SEC.AUTH_SIGNATURE"]),
        f"{CONSUMER}\n1700000000123\n7\n".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


def test_items_missing_price_or_name_are_skipped(key):
    c, _ = _connector(key)
    result = c.search("mixed")
    assert [o.retailer_item_id for o in result.offers] == ["1", "4"]
    assert result.skipped == 2


def test_http_error_raises_connector_error_without_secrets(key):
    c, seen = _connector(key)
    with pytest.raises(ConnectorError) as exc:
        c.search("unauthorized")
    sig = seen[0].headers["WM_SEC.AUTH_SIGNATURE"]
    assert "401" in str(exc.value)
    assert CONSUMER not in str(exc.value) and sig not in str(exc.value)
    assert exc.value.__cause__ is None


def test_accepts_pem_and_base64_der_keys(key):
    for text in (_pem(key), _der_b64(key)):
        c = WalmartConnector(CONSUMER, text, "7")
        assert c._sign("1") == sign(CONSUMER, "1", "7", key)


def test_repr_never_exposes_key_or_consumer_id(key):
    pk = _der_b64(key)
    c = WalmartConnector(CONSUMER, pk, "7", publisher_id="PUB9")
    for text in (repr(c), str(c)):
        assert CONSUMER not in text and pk not in text and "PUB9" not in text


def test_bad_key_error_has_no_key_text():
    with pytest.raises(ConnectorError) as exc:
        WalmartConnector(CONSUMER, "bm90YWtleQ==", "7")
    assert "bm90YWtleQ" not in str(exc.value)


def test_registry_builds_walmart_from_env(key):
    env = {
        "WALMART_CONSUMER_ID": CONSUMER,
        "WALMART_PRIVATE_KEY": _der_b64(key),
        "WALMART_KEY_VERSION": "7",
    }
    c = registry.get("walmart", env)
    assert isinstance(c, WalmartConnector)
    assert c.name == "walmart" and c.records_price_history is False
    assert "walmart" in registry.available(env)


def test_max_cache_age_is_24_hours(key):
    from datetime import timedelta

    assert WalmartConnector(CONSUMER, _der_b64(key), "7").max_cache_age == timedelta(
        hours=24
    )


def test_non_rsa_pem_key_rejected():
    from cryptography.hazmat.primitives.asymmetric import ec

    pem = (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )
    with pytest.raises(ConnectorError, match="RSA"):
        WalmartConnector(CONSUMER, pem, "7")


def test_non_list_items_raises_connector_error(key):
    c, _ = _connector(key)
    with pytest.raises(ConnectorError, match="shape"):
        c.search("badshape")


def test_non_dict_item_counted_as_skipped(key):
    c, _ = _connector(key)
    c._client = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                json={
                    "items": ["junk", None, {"itemId": 5, "name": "A", "salePrice": 1}]
                },
            )
        )
    )
    result = c.search("x")
    assert result.skipped == 2 and [o.retailer_item_id for o in result.offers] == ["5"]
