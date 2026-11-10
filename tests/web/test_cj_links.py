from types import SimpleNamespace
from urllib.parse import quote

import pytest

from catalog.links import affiliate_programs, link_attrs, outbound_url
from catalog.retailers import CJ_RETAILERS, available_retailers, display_name

URL = "https://www.walgreens.com/store/c/milk/ID=1?a=1&b=2"
NONE = {"walmart_impact_publisher_id": None, "sovrn_api_key": None, "amazon_associates_tag": None}


def offer(retailer="cj-walgreens", url=URL):
    return SimpleNamespace(retailer=retailer, url=url, pk=3)


def test_cj_deep_link_route():
    got = outbound_url(offer(), {"cj_website_id": "WEB1"})
    assert got == f"https://www.anrdoezrs.net/links/WEB1/type/dlg/{quote(URL, safe='')}"
    attrs = link_attrs(offer(), {"cj_website_id": "WEB1"})
    assert attrs["sponsored"] is True and attrs["href"] == got


def test_cj_beats_sovrn():
    got = outbound_url(offer(), {"cj_website_id": "WEB1", "sovrn_api_key": "K"})
    assert got.startswith("https://www.anrdoezrs.net/links/WEB1/")


def test_no_route_without_website_id():
    assert outbound_url(offer(), {"cj_website_id": None, "sovrn_api_key": "K"}).startswith("https://sovrn.co?key=K")
    assert outbound_url(offer(), {"cj_website_id": ""}) == URL
    assert link_attrs(offer(), {})["sponsored"] is False


def test_cj_id_only_for_cj_codes():
    assert outbound_url(offer("target", "https://t.com/p"), {"cj_website_id": "W"}) == "https://t.com/p"


def test_programs_lists_cj():
    assert affiliate_programs({**NONE, "sovrn_api_key": "k", "cj_website_id": "w"}) == [
        "Sovrn Commerce",
        "CJ Affiliate",
    ]


@pytest.mark.parametrize(
    "code,name",
    [
        ("cj-walgreens", "Walgreens"),
        ("cj-iherb", "iHerb"),
        ("cj-thrive-market", "Thrive Market"),
        ("cj-bj-s-wholesale-club", "BJ's Wholesale Club"),
        ("cj-vitacost-com", "Vitacost"),
        ("cj-some-new-shop", "Some New Shop"),
    ],
)
def test_cj_display_names(code, name):
    assert display_name(code) == name
    assert "cj-" not in display_name(code)


def test_cj_retailers_dict():
    assert len(CJ_RETAILERS) == 5


def test_disclosure_lists_cj_when_configured(client, settings):
    settings.AFFILIATE = {**NONE, "cj_website_id": "W"}
    assert "CJ Affiliate" in client.get("/disclosure").content.decode()
    settings.AFFILIATE = {**NONE}
    assert "CJ Affiliate" not in client.get("/disclosure").content.decode()


@pytest.mark.django_db
def test_retailer_filter_lists_cj_with_display_name():
    from tests.catalog.helpers import NOW, make_offer

    make_offer("cj-iherb", item="a")
    make_offer("walmart", item="b")
    codes = available_retailers(NOW)
    assert codes == ["cj-iherb", "walmart"]  # ordered by display name (case-insensitive)
    assert [display_name(c) for c in codes] == ["iHerb", "Walmart"]
