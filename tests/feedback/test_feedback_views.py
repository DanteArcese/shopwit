import re

import pytest

from feedback.models import Feedback
from tests.feedback.conftest import APP, PUBLIC

pytestmark = pytest.mark.django_db


def post(client, data, ip="203.0.113.5"):
    payload = {"kind": "bug", "message": "", "company": ""}
    payload.update(data)
    return client.post("/feedback", payload, HTTP_HOST=APP, HTTP_CF_CONNECTING_IP=ip)


def body(response):
    # The CSRF token differs per response; everything else must match.
    return re.sub(
        r'name="csrfmiddlewaretoken" value="[^"]*"', "", response.content.decode()
    )


def test_form_renders_on_app_host(client):
    r = client.get("/feedback", HTTP_HOST=APP)
    assert r.status_code == 200
    html = r.content.decode()
    assert "csrfmiddlewaretoken" in html
    for value in ("store", "data", "bug", "idea", "other"):
        assert f'value="{value}"' in html
    assert 'name="company"' in html  # honeypot


@pytest.mark.parametrize("method", ["get", "post"])
def test_not_reachable_on_apex(client, method):
    r = getattr(client, method)("/feedback", HTTP_HOST=PUBLIC)
    assert r.status_code == 404


def test_footer_links_to_feedback(client):
    html = client.get("/", HTTP_HOST=APP).content.decode()
    assert 'href="/feedback"' in html


def test_bug_saved(client):
    r = post(
        client,
        {"kind": "bug", "message": "Search is broken", "reply_email": "Me@Example.com"},
    )
    assert r.status_code == 200
    fb = Feedback.objects.get()
    assert (fb.kind, fb.message, fb.status) == ("bug", "Search is broken", "new")
    assert fb.reply_email == "me@example.com"
    assert fb.offer is None and fb.store_key == ""


@pytest.mark.parametrize("kind", ["bug", "idea", "other", "data"])
def test_message_required_except_store(client, kind):
    r = post(client, {"kind": kind, "message": "   "})
    assert r.status_code == 200
    assert "Enter a message" in r.content.decode()
    assert not Feedback.objects.exists()


def test_message_length_limit(client):
    r = post(client, {"kind": "idea", "message": "x" * 2001})
    assert "at most 2000" in r.content.decode()
    assert not Feedback.objects.exists()
    post(client, {"kind": "idea", "message": "x" * 2000})
    assert Feedback.objects.count() == 1


def test_store_requires_name_not_message(client):
    r = post(client, {"kind": "store", "store_name": ""})
    assert "Enter the store" in r.content.decode()
    assert not Feedback.objects.exists()
    post(
        client,
        {"kind": "store", "store_name": "Trader Joe's", "website": "traderjoes.com"},
    )
    fb = Feedback.objects.get()
    assert (fb.store_name, fb.store_key, fb.website) == (
        "Trader Joe's",
        "trader joes",
        "traderjoes.com",
    )


def test_store_name_and_website_limits(client):
    r = post(client, {"kind": "store", "store_name": "a" * 121})
    assert "at most 120" in r.content.decode()
    r = post(client, {"kind": "store", "store_name": "Aldi", "website": "a" * 201})
    assert "at most 200" in r.content.decode()
    assert not Feedback.objects.exists()


def test_store_name_of_only_filler_rejected(client):
    r = post(client, {"kind": "store", "store_name": "The !!!"})
    assert "Enter the store" in r.content.decode()
    assert not Feedback.objects.exists()


def test_invalid_reply_email(client):
    r = post(client, {"kind": "bug", "message": "hi", "reply_email": "nope"})
    assert "valid email" in r.content.decode()
    assert not Feedback.objects.exists()


def test_unknown_kind_rejected(client):
    post(client, {"kind": "spam", "message": "hi"})
    assert not Feedback.objects.exists()


def test_report_link_prefills_offer(client, offer):
    r = client.get(f"/feedback?type=wrong-data&offer={offer.pk}", HTTP_HOST=APP)
    html = r.content.decode()
    assert re.search(r'value="data"[^>]*checked', html)
    assert f'name="offer" value="{offer.pk}"' in html
    assert "Acme 2% Milk, 1 L" in html


def test_prefill_ignores_unknown_offer_and_type(client, db):
    r = client.get("/feedback?type=nonsense&offer=999999", HTTP_HOST=APP)
    html = r.content.decode()
    assert r.status_code == 200
    assert 'name="offer" value="999999"' not in html
    r = client.get("/feedback?offer=abc", HTTP_HOST=APP)
    assert r.status_code == 200


def test_data_report_links_offer(client, offer):
    post(
        client,
        {"kind": "data", "message": "Size is 2 L, not 1 L", "offer": str(offer.pk)},
    )
    fb = Feedback.objects.get()
    assert fb.offer == offer


def test_offer_ignored_unless_data_or_missing(client, offer):
    post(client, {"kind": "bug", "message": "x", "offer": str(offer.pk)})
    post(client, {"kind": "data", "message": "y", "offer": "999999"})
    assert list(Feedback.objects.order_by("id").values_list("offer_id", flat=True)) == [
        None,
        None,
    ]


def test_observed_rows_have_report_link():
    from pathlib import Path

    tpl = Path(__file__).resolve().parents[2] / "web/templates/web/_observed_row.html"
    assert "?type=wrong-data&amp;offer={{ row.offer.pk }}" in tpl.read_text()


def test_honeypot_drops_silently_with_identical_response(client):
    real = post(client, {"kind": "bug", "message": "real"}, ip="198.51.100.1")
    trap = post(
        client,
        {"kind": "bug", "message": "spam", "company": "Spam Inc"},
        ip="198.51.100.2",
    )
    assert trap.status_code == real.status_code == 200
    assert body(trap) == body(real)
    assert list(Feedback.objects.values_list("message", flat=True)) == ["real"]


def test_identical_response_for_repeat_store_request(client):
    first = post(client, {"kind": "store", "store_name": "Aldi"}, ip="198.51.100.1")
    again = post(client, {"kind": "store", "store_name": "ALDI"}, ip="198.51.100.2")
    other = post(client, {"kind": "idea", "message": "dark mode"}, ip="198.51.100.3")
    assert body(first) == body(again) == body(other)
    assert "Aldi" not in body(again)


def test_rate_limit_per_visitor(client):
    for i in range(5):
        assert post(client, {"message": f"m{i}"}, ip="198.51.100.9").status_code == 200
    blocked = post(client, {"message": "m6"}, ip="198.51.100.9")
    assert blocked.status_code == 429
    assert Feedback.objects.count() == 5
    # A different visitor is unaffected.
    assert post(client, {"message": "other"}, ip="198.51.100.10").status_code == 200
    assert Feedback.objects.count() == 6


def test_global_daily_limit(client, monkeypatch):
    from feedback import views

    monkeypatch.setattr(views, "GLOBAL_LIMIT", 2)
    for i in range(2):
        post(client, {"message": f"m{i}"}, ip=f"198.51.100.{i}")
    r = post(client, {"message": "m3"}, ip="198.51.100.50")
    assert r.status_code == 503
    assert Feedback.objects.count() == 2


def test_csrf_enforced(db):
    from django.test import Client

    c = Client(enforce_csrf_checks=True)
    r = c.post("/feedback", {"kind": "bug", "message": "x"}, HTTP_HOST=APP)
    assert r.status_code == 403
    assert not Feedback.objects.exists()
