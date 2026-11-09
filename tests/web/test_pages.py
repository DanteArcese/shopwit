import pytest

from catalog.links import affiliate_programs

NONE = {"walmart_impact_publisher_id": None, "sovrn_site_key": None, "amazon_associates_tag": None}


def test_terms_marked_draft(client):
    body = client.get("/terms").content.decode()
    assert "Draft — pending review by the maintainer and legal counsel. Not yet in effect." in body
    assert "hello@unitpricer.com" in body
    assert "ODbL" in body and "AGPL" in body


def test_about_names_contacts(client):
    body = client.get("/about").content.decode()
    assert "mailto:hello@unitpricer.com" in body and "mailto:dante@unitpricer.com" in body
    assert "Dante Arcese" in body


def test_amazon_sentence_only_with_tag(client, settings):
    settings.AFFILIATE = {**NONE}
    body = client.get("/disclosure").content.decode()
    assert "As an Amazon Associate" not in body and "Amazon Associates Program" not in body
    settings.AFFILIATE = {**NONE, "amazon_associates_tag": "unitpricer-20"}
    body = client.get("/disclosure").content.decode()
    assert "As an Amazon Associate I earn from qualifying purchases." in body


def test_disclosure_lists_no_programs_when_none_configured(client, settings):
    settings.AFFILIATE = {**NONE}
    body = client.get("/disclosure").content.decode()
    assert "does not currently participate in any affiliate program" in body
    assert "Sovrn" not in body and "Impact" not in body


def test_affiliate_programs_order():
    assert affiliate_programs(
        {"walmart_impact_publisher_id": "1", "sovrn_site_key": "k", "amazon_associates_tag": "t"}
    ) == [
        "Amazon Associates Program",
        "Walmart's affiliate program through Impact",
        "Sovrn Commerce",
    ]
    assert affiliate_programs(NONE) == []


def test_disclosure_live_search_follows_config(client, settings, monkeypatch):
    for var in ("WALMART_CONSUMER_ID", "WALMART_PRIVATE_KEY", "WALMART_KEY_VERSION"):
        monkeypatch.delenv(var, raising=False)
    settings.LIVE_OFF_ENABLED = False
    body = client.get("/disclosure").content.decode()
    assert "ask Walmart" not in body and "Walmart and" not in body


@pytest.mark.parametrize("path", ["/terms", "/about", "/disclosure"])
def test_pages_set_no_cookies(client, path):
    assert not client.get(path).cookies


@pytest.mark.django_db
def test_footer_links(client):
    body = client.get("/").content.decode()
    for href in ("/disclosure", "/privacy", "/terms", "/about", "/feedback"):
        assert f'href="{href}"' in body
    assert "https://github.com/DanteArcese/unitpricer" in body
