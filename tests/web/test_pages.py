import pytest

from catalog.links import affiliate_programs

NONE = {"walmart_impact_publisher_id": None, "sovrn_api_key": None, "amazon_associates_tag": None}


def test_terms_final(client):
    body = client.get("/terms").content.decode()
    assert "Draft" not in body and "[Governing law" not in body
    assert "Effective November 9, 2026." in body
    assert "laws of the Commonwealth of Massachusetts" in body
    assert "small-claims court" in body
    assert "at least 13 years old" in body
    assert "hello@unitpricer.com" in body
    assert "ODbL" in body and "AGPL" in body


def test_about_names_contacts(client):
    body = client.get("/about").content.decode()
    assert "mailto:hello@unitpricer.com" in body and "mailto:dante@unitpricer.com" in body
    assert "Dante Arcese" in body


def test_about_ai_sentence(client):
    body = client.get("/about").content.decode()
    assert "No AI ranks results or writes product or price text." in body
    assert "rewrite or generate anything" not in body


def test_terms_feedback_matches_privacy(client):
    body = client.get("/terms").content.decode()
    feedback = body.split("<h2>Feedback</h2>", 1)[1].split("<h2>", 1)[0]
    assert "Reports of a wrong price or size stay private." in feedback
    assert "approved bug reports, ideas and store requests" in feedback
    assert "without your email address" in feedback
    assert "you allow us to post it publicly as an issue" not in feedback


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
        {"walmart_impact_publisher_id": "1", "sovrn_api_key": "k", "amazon_associates_tag": "t"}
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
