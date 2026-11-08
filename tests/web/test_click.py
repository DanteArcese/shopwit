import pytest

from catalog.models import ClickEvent, Offer


@pytest.mark.django_db
def test_click_records_only_offer_and_retailer(client, milk_rows):
    offer = Offer.objects.first()
    resp = client.post(f"/click?o={offer.pk}", HTTP_USER_AGENT="UA-secret", REMOTE_ADDR="9.9.9.9")
    assert resp.status_code == 204
    event = ClickEvent.objects.get()
    assert event.offer_id == offer.pk
    assert event.retailer == offer.retailer
    assert {f.name for f in ClickEvent._meta.get_fields()} == {"id", "offer", "retailer", "created_at"}
    assert "UA-secret" not in repr(event.__dict__) and "9.9.9.9" not in repr(event.__dict__)


@pytest.mark.django_db
@pytest.mark.parametrize("o", ["999999", "abc", "", "-1", "99999999999999999999999"])
def test_click_unknown_id_204(client, o):
    resp = client.post(f"/click?o={o}")
    assert resp.status_code == 204
    assert ClickEvent.objects.count() == 0


@pytest.mark.django_db
def test_click_missing_param_204(client):
    assert client.post("/click").status_code == 204


@pytest.mark.django_db
def test_click_get_is_405(client):
    assert client.get("/click?o=1").status_code == 405


@pytest.mark.django_db
def test_click_csrf_exempt(milk_rows):
    from django.test import Client

    offer = Offer.objects.first()
    c = Client(enforce_csrf_checks=True)
    assert c.post(f"/click?o={offer.pk}").status_code == 204
    assert ClickEvent.objects.count() == 1


@pytest.mark.django_db
def test_click_survives_offer_deletion(client, milk_rows):
    offer = Offer.objects.first()
    client.post(f"/click?o={offer.pk}")
    offer.delete()
    event = ClickEvent.objects.get()
    assert event.offer_id is None and event.retailer
