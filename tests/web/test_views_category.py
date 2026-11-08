import re

import pytest

pytestmark = pytest.mark.usefixtures("frozen_now")


def test_category_page_lists_ranked_rows(client, milk_rows):
    html = client.get("/c/dairy/milk/").content.decode()
    assert html.index("Bravo Whole Milk") < html.index("Cargo Milk") < html.index("Acme Organic")
    assert "$1.00" in html
    assert "Bravo" in html
    assert "1 L" in html
    assert "/ L" in html or "/ fl oz" in html
    assert "Fake" in html  # retailer display name
    assert 'hx-target="#results"' in html
    assert 'hx-push-url="true"' in html
    assert 'rel="noopener noreferrer"' in html
    assert 'href="https://example.com/fake/b"' in html


def test_filter_url_round_trip(client, milk_rows):
    html = client.get("/c/dairy/milk/?fat=2&organic=true").content.decode()
    assert "Acme Organic" in html
    assert "Bravo Whole" not in html and "Cargo Milk" not in html
    assert re.search(r'<input[^>]*name="organic"[^>]*value="true"[^>]*checked', html)
    assert re.search(r'<option[^>]*value="2"[^>]*selected', html)


def test_htmx_request_returns_partial(client, milk_rows):
    resp = client.get("/c/dairy/milk/", HTTP_HX_REQUEST="true")
    html = resp.content.decode()
    assert "<html" not in html
    assert "Bravo Whole Milk" in html


def test_price_as_of_present_for_each_row(client, milk_rows):
    html = client.get("/c/dairy/milk/").content.decode()
    assert html.count("price as of") >= 4
    assert html.count('datetime="2026-11-07T12:00:00+00:00"') >= 4
    assert "Nov 7, 12:00 PM" in html


def test_unknown_label_shown(client, milk_rows):
    html = client.get("/c/dairy/milk/").content.decode()
    assert html.count("fat unknown") == 1
    assert "Cargo Milk" in html


def test_cant_rank_section(client, milk_rows):
    html = client.get("/c/dairy/milk/").content.decode()
    assert "Can't rank (1)" in html or "Can&#x27;t rank (1)" in html
    assert "Mystery Whole Milk" in html
    assert "size unknown" in html


def test_empty_state_with_clear_link(client, milk_rows):
    html = client.get("/c/dairy/milk/?fat=skim").content.decode()
    assert "No fresh prices match these filters" in html
    assert 'href="/c/dairy/milk/"' in html


def test_unknown_slug_404(client, milk_rows):
    assert client.get("/c/nope/").status_code == 404


def test_inactive_slug_404(client, milk, milk_rows):
    milk.active = False
    milk.save()
    assert client.get("/c/dairy/milk/").status_code == 404


def test_metric_user_sees_metric_labels(client, milk_rows):
    html = client.get("/c/dairy/milk/", HTTP_ACCEPT_LANGUAGE="de-DE").content.decode()
    assert "/ L" in html
    assert "fl oz" not in html


def test_imperial_user_sees_imperial_labels(client, milk_rows):
    html = client.get("/c/dairy/milk/", HTTP_ACCEPT_LANGUAGE="en-US").content.decode()
    assert "/ fl oz" in html


def test_size_inputs_are_display_units(client, milk_rows):
    # metric: size_min=2 means 2 L; all 1 L products drop out
    html = client.get("/c/dairy/milk/?size_min=2", HTTP_ACCEPT_LANGUAGE="de-DE").content.decode()
    assert "No fresh prices match these filters" in html
    # imperial: 30 fl oz is about 887 mL, so 1 L (33.8 fl oz) products stay
    html = client.get("/c/dairy/milk/?size_min=30", HTTP_ACCEPT_LANGUAGE="en-US").content.decode()
    assert "Bravo Whole Milk" in html


def test_store_pref_filters_results(client, load, milk_rows):
    from tests.catalog.helpers import raw

    load([raw(title="Walmart Whole Milk, 1 L", price="1.20", item="w", retailer="walmart")])
    client.post("/settings", {"unit_system": "metric", "stores": ["walmart"]})
    html = client.get("/c/dairy/milk/").content.decode()
    assert "Walmart Whole Milk" in html
    assert "Bravo Whole Milk" not in html


def test_q_rest_note(client, milk_rows):
    html = client.get("/c/dairy/milk/?q_rest=lactose").content.decode()
    assert "also matching" in html and "lactose" in html


def test_q_rest_escaped(client, milk_rows):
    html = client.get("/c/dairy/milk/?q_rest=<script>x</script>").content.decode()
    assert "<script>x" not in html


def test_saved_store_without_fresh_offers_shown_and_empty_state(client, milk_rows):
    client.post("/settings", {"unit_system": "metric", "stores": ["costco"]})
    html = client.get("/c/dairy/milk/").content.decode()
    assert "No fresh prices match these filters" in html
    assert re.search(r'<input[^>]*name="store"[^>]*value="costco"[^>]*checked[^>]*> Costco \(no current prices\)', html)


def test_empty_state_shown_when_only_unranked(client, load):
    from tests.catalog.helpers import raw

    load([raw(title="Mystery Whole Milk", price="3.00", item="d", brand="M")])
    html = client.get("/c/dairy/milk/").content.decode()
    assert "No fresh prices match these filters" not in html
    assert "None of these can be ranked by price per" in html
    assert "Mystery Whole Milk" in html


def test_cap_notice(client, milk_rows, monkeypatch):
    import catalog.browse as b

    real = b.browse
    monkeypatch.setattr("web.views.browse", lambda *a, **k: real(*a, limit=2))
    html = client.get("/c/dairy/milk/").content.decode()
    assert "Showing the top 2 of 3" in html
