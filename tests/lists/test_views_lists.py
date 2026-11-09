from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from catalog.models import FetchJob, Product
from connectors.live import SourceInfo
from lists import resolve as rs
from lists.spec import ItemSpec, ListSpec, new_id
from lists.store import AccountListStore, SessionListStore
from tests.catalog.helpers import NOW, raw
from tests.catalog.test_generic_match import make_generic_offer

GAL = Decimal("3785.412")


@pytest.fixture(autouse=True)
def _frozen(monkeypatch):
    monkeypatch.setattr("web.views.now", lambda: NOW)
    monkeypatch.setattr(rs, "live_source_info", lambda **kw: {})
    monkeypatch.setattr("web.search.live_source_info", lambda **kw: {})


def milk_item(n=1, fat="2", amount=GAL, pinned=None):
    return ItemSpec(
        id=f"{n:032x}",
        category="dairy/milk",
        filters={"fat": fat} if fat else {},
        target_measure="volume_ml",
        target_amount=amount,
        pinned_product=pinned,
    )


def make_list(*items, name="Groceries", list_id=None):
    return ListSpec(
        id=list_id or new_id(), name=name, items=tuple(items), updated_at=NOW
    )


def seed(client, *items, name="Groceries"):
    spec = make_list(*items, name=name)
    session = client.session
    SessionListStore(session).save(spec)
    session.save()
    return spec


def imperial(client):
    session = client.session
    session["prefs"] = {"unit_system": "imperial", "stores": None}
    session.save()


@pytest.fixture
def milk_offers(milk, load):
    load(
        milk,
        [
            raw(
                title="Acme 2% Milk, 64 fl oz",
                price="3.00",
                item="a64",
                retailer="walmart",
                brand="Acme",
            ),
            raw(
                title="Acme 2% Milk, 128 fl oz",
                price="6.40",
                item="a128",
                retailer="walmart",
                brand="Acme",
            ),
            raw(
                title="Bravo 2% Milk, 128 fl oz",
                price="6.50",
                item="b128",
                retailer="target",
                brand="Bravo",
            ),
        ],
    )


def detail_url(spec):
    return f"/lists/{spec.id}/"


# ---- add links, cookies --------------------------------------------------------------------


def test_category_page_has_add_link_with_current_filters(client, milk_offers):
    html = client.get("/c/dairy/milk/?fat=2", HTTP_HX_REQUEST="true").content.decode()
    assert 'href="/lists/add?category=dairy%2Fmilk&amp;fat=2"' in html
    full = client.get("/c/dairy/milk/?fat=2").content.decode()
    assert 'href="/lists/add?category=dairy%2Fmilk&amp;fat=2"' in full


def test_browsing_sets_no_cookies(client, milk_offers):
    make_generic_offer("Fizz Soda", "2", dim="volume", amount=1000)
    for path, extra in [
        ("/c/dairy/milk/", {}),
        ("/c/dairy/milk/", {"HTTP_HX_REQUEST": "true"}),
        ("/search?q=fizz+soda", {}),
        ("/search?q=fizz+soda", {"HTTP_HX_REQUEST": "true"}),
    ]:
        resp = client.get(path, **extra)
        assert resp.status_code == 200
        assert "Set-Cookie" not in resp.headers and not resp.cookies, path


def test_generic_group_has_add_link(client, db):
    make_generic_offer("Fizz Soda A", "2", dim="volume", amount=1000, brand="Fizzco")
    make_generic_offer("Fizz Soda Cans", "5", dim="count", amount=12)
    html = client.get("/search?q=fizz+soda").content.decode()
    assert "kind=generic&amp;q=fizz+soda&amp;dim=volume" in html
    assert "dim=count" in html
    html = client.get("/search?q=fizz+soda&brand=fizzco").content.decode()
    assert "kind=generic&amp;q=fizz+soda&amp;dim=volume&amp;brand=fizzco" in html


def test_add_page_sets_session_only_on_post(client, milk_offers):
    resp = client.get("/lists/add?category=dairy%2Fmilk&fat=2")
    assert resp.status_code == 200
    assert "sessionid" not in resp.cookies


def test_add_page_prefills_from_top_offer(client, milk_offers):
    imperial(client)
    html = client.get("/lists/add?category=dairy%2Fmilk&fat=2").content.decode()
    assert "Milk · fat 2" in html
    assert (
        'id="amount" name="amount" inputmode="decimal" value="64"' in html
    )  # best unit price: 64 fl oz


# ---- add -----------------------------------------------------------------------------------


def test_add_creates_session_list_and_item(client, milk_offers):
    imperial(client)
    resp = client.post(
        "/lists/add",
        {
            "category": "dairy/milk",
            "fat": "2",
            "amount": "128",
            "unit": "floz",
            "list": "new",
        },
    )
    assert resp.status_code == 302
    lists = SessionListStore(client.session).lists()
    assert len(lists) == 1 and lists[0].name == "My list"
    assert resp["Location"] == detail_url(lists[0])
    (item,) = lists[0].items
    assert item.target_measure == "volume_ml" and item.target_amount == GAL
    assert dict(item.filters) == {"fat": "2"}


def test_add_appends_to_existing_list(client, milk_offers):
    spec = seed(client, milk_item(1))
    resp = client.post(
        "/lists/add",
        {
            "category": "dairy/milk",
            "fat": "2",
            "organic": "true",
            "amount": "1",
            "unit": "gal",
            "list": spec.id,
        },
    )
    assert resp.status_code == 302
    (saved,) = SessionListStore(client.session).lists()
    assert len(saved.items) == 2 and saved.id == spec.id


def test_add_rejects_invalid_filter_and_bad_amount(client, milk_offers):
    imperial(client)
    bad = client.post(
        "/lists/add",
        {
            "category": "dairy/milk",
            "fat": "banana",
            "amount": "1",
            "unit": "gal",
            "list": "new",
        },
    )
    assert bad.status_code == 400
    assert client.get("/lists/add?category=dairy%2Fmilk&fat=banana").status_code == 400
    for amount in ("0", "-2", "abc", ""):
        resp = client.post(
            "/lists/add",
            {
                "category": "dairy/milk",
                "fat": "2",
                "amount": amount,
                "unit": "gal",
                "list": "new",
            },
        )
        assert resp.status_code == 200
        assert "Enter an amount greater than 0" in resp.content.decode()
    assert SessionListStore(client.session).lists() == []
    assert client.get("/lists/add?category=nope").status_code == 404
    assert client.get("/lists/add").status_code == 404


def test_add_reports_full_list_and_too_many_lists(client, milk_offers):
    from lists import spec as sp

    imperial(client)
    full = seed(client, *[milk_item(n) for n in range(1, sp.MAX_ITEMS + 1)])
    resp = client.post(
        "/lists/add",
        {"category": "dairy/milk", "amount": "1", "unit": "gal", "list": full.id},
    )
    assert "This list is full (100 items)" in resp.content.decode()
    for n in range(sp.MAX_LISTS - 1):
        seed(client, name=f"L{n}")
    resp = client.post(
        "/lists/add",
        {"category": "dairy/milk", "amount": "1", "unit": "gal", "list": "new"},
    )
    assert "You have the maximum of 20 lists" in resp.content.decode()


def test_add_generic_item_from_group(client, db):
    imperial(client)
    make_generic_offer("Fizz Soda A", "2", dim="volume", amount=1000, brand="Fizzco")
    make_generic_offer("Fizz Soda B", "3", dim="volume", amount=2000)
    page = client.get("/lists/add?kind=generic&q=Fizz+Soda&dim=volume&brand=fizzco")
    html = page.content.decode()
    assert "&#x27;fizz soda&#x27; · brand fizzco · by volume" in html
    assert 'value="33.81"' in html  # 1000 mL in fl oz, first imperial unit
    resp = client.post(
        "/lists/add",
        {
            "kind": "generic",
            "q": "fizz soda",
            "dim": "volume",
            "brand": "fizzco",
            "amount": "2",
            "unit": "l",
            "list": "new",
        },
    )
    assert resp.status_code == 302
    (item,) = SessionListStore(client.session).lists()[0].items
    assert (item.kind, item.query, item.target_measure) == (
        "generic",
        "fizz soda",
        "volume",
    )
    assert item.target_amount == Decimal("2000.000") or item.target_amount == Decimal(
        2000
    )
    assert dict(item.facets) == {"brand": ("fizzco",)}
    for params in (
        {"q": "fizz", "dim": "bogus"},
        {"q": "fizz", "dim": "volume", "brand_tier": "banana"},
        {"q": "", "dim": "volume"},
    ):
        assert (
            client.get("/lists/add", {"kind": "generic", **params}).status_code == 400
        )
        bad = client.post(
            "/lists/add", {"kind": "generic", "amount": "1", "unit": "l", **params}
        )
        assert bad.status_code == 400
    shown = client.get(
        detail_url(SessionListStore(client.session).lists()[0])
    ).content.decode()
    assert "fizz soda" in shown


# ---- detail --------------------------------------------------------------------------------


def two_item_list(client):
    imperial(client)
    return seed(
        client, milk_item(1, fat="2"), milk_item(2, fat="2", amount=Decimal("1892.706"))
    )


def test_detail_shows_fit_totals_single_split(client, milk_offers):
    spec = two_item_list(client)
    html = client.get(detail_url(spec)).content.decode()
    assert "2 &times; 64 fl oz at Walmart: $6.00" in html
    assert "or 1 &times; 128 fl oz (1 gal): $6.40" in html
    assert "Totals by store" in html and "Target" in html and "Walmart" in html
    assert (
        "Cheapest single store: <strong>Walmart $9.00</strong>" in html
    )  # 6.00 + 3.00
    assert "No single store has every item" not in html
    assert "Cheapest single store" in html
    assert "Cheapest split" in html
    assert "(up to 2 stores)" in html


def test_detail_split_param_and_invalid_values(client, milk_offers):
    spec = two_item_list(client)
    assert (
        "(up to 3 stores)" in client.get(detail_url(spec) + "?split=3").content.decode()
    )
    assert (
        "(up to 2 stores)"
        in client.get(detail_url(spec) + "?split=banana").content.decode()
    )
    assert (
        "Cheapest split:"
        in client.get(detail_url(spec) + "?split=all").content.decode()
    )


def test_detail_store_override(client, milk_offers):
    spec = two_item_list(client)
    html = client.get(detail_url(spec) + "?store=target").content.decode()
    assert "at Target" in html and "at Walmart" not in html


def test_detail_price_as_of_and_disclosure(client, milk_offers):
    spec = two_item_list(client)
    html = client.get(detail_url(spec)).content.decode()
    assert html.count("price as of") >= 2
    assert 'datetime="2026-11-07T12:00:00+00:00"' in html
    assert "We may earn a commission" in html
    assert 'rel="' in html and "data-offer=" in html  # {% outbound %} link


def test_amazon_offer_keeps_disclaimer(client, milk, load):
    load(
        milk,
        [
            raw(
                title="Acme 2% Milk, 1 gal",
                price="4.00",
                item="x",
                retailer="amazon",
                brand="Acme",
            )
        ],
    )
    spec = seed(client, milk_item(1))
    html = client.get(detail_url(spec)).content.decode()
    assert "As an Amazon Associate" in html
    assert "accurate as of the date/time indicated" in html


def test_unresolved_item_shows_reason(client, milk_offers):
    imperial(client)
    spec = seed(client, milk_item(1, fat="2"), milk_item(2, fat="banana"))
    html = client.get(detail_url(spec)).content.decode()
    assert "Filter no longer valid: fat" in html
    assert "1 item without a price is left out of the totals." in html


def test_mixed_currency_message_shown(client, milk_offers, monkeypatch):
    # Offers outside the site currency are never fresh, so force the resolver's flag.
    import dataclasses

    import lists.views as lv

    real = lv.resolve_list
    monkeypatch.setattr(
        lv,
        "resolve_list",
        lambda *a, **k: dataclasses.replace(
            real(*a, **k),
            mixed_currency=True,
            totals=[],
            single=None,
            split=None,
            currency=None,
        ),
    )
    spec = seed(client, milk_item(1))
    html = client.get(detail_url(spec)).content.decode()
    assert "Can&#x27;t total: prices in more than one currency" in html
    assert "Totals by store" not in html and "Cheapest" not in html


# ---- edit, pin, remove, rename, delete -----------------------------------------------------


def test_edit_target_pin_unpin_remove_rename_delete(client, milk_offers):
    imperial(client)
    spec = seed(client, milk_item(1), milk_item(2))
    base = f"/lists/{spec.id}"
    item = spec.items[0].id

    r = client.post(f"{base}/items/{item}/target", {"amount": "2", "unit": "gal"})
    assert r.status_code == 302
    saved = SessionListStore(client.session).get(spec.id)
    assert saved.items[0].target_amount == GAL * 2

    pid = Product.objects.get(offers__retailer_item_id="b128").pk
    assert client.post(f"{base}/items/{item}/pin", {"product": pid}).status_code == 302
    assert SessionListStore(client.session).get(spec.id).items[0].pinned_product == pid
    assert "Unpin" in client.get(base + "/").content.decode()
    assert client.post(f"{base}/items/{item}/unpin").status_code == 302
    assert SessionListStore(client.session).get(spec.id).items[0].pinned_product is None

    assert client.post(f"{base}/rename", {"name": "Weekly"}).status_code == 302
    assert SessionListStore(client.session).get(spec.id).name == "Weekly"

    assert client.post(f"{base}/items/{item}/remove").status_code == 302
    assert [i.id for i in SessionListStore(client.session).get(spec.id).items] == [
        spec.items[1].id
    ]

    resp = client.post(f"{base}/delete", {"confirm": "yes"})
    assert resp.status_code == 302 and resp["Location"] == "/lists/"
    assert SessionListStore(client.session).lists() == []


def test_edit_target_bad_amount_shows_error(client, milk_offers):
    imperial(client)
    spec = seed(client, milk_item(1))
    r = client.post(
        f"/lists/{spec.id}/items/{spec.items[0].id}/target",
        {"amount": "0", "unit": "gal"},
    )
    assert "Enter an amount greater than 0" in r.content.decode()
    assert (
        client.post(
            f"/lists/{spec.id}/items/{spec.items[0].id}/target",
            {"amount": "1", "unit": "parsec"},
        ).status_code
        == 400
    )


def test_pin_rejects_product_from_other_category(client, milk_offers, tp, load):
    load(
        tp,
        [
            raw(
                title="Soft Tissue 12 Rolls",
                price="9.00",
                item="tp1",
                retailer="walmart",
                brand="Soft",
            )
        ],
    )
    imperial(client)
    spec = seed(client, milk_item(1))
    other = Product.objects.get(offers__retailer_item_id="tp1").pk
    base = f"/lists/{spec.id}/items/{spec.items[0].id}"
    assert client.post(f"{base}/pin", {"product": other}).status_code == 400
    assert client.post(f"{base}/pin", {"product": "x"}).status_code == 400
    assert client.post(f"{base}/pin", {"product": "99999999"}).status_code == 400
    assert SessionListStore(client.session).get(spec.id).items[0].pinned_product is None


def test_index_lists_and_empty_state(client, db):
    html = client.get("/lists/").content.decode()
    assert "No lists yet" in html
    r = client.post("/lists/new", {"name": "Party"})
    assert r.status_code == 302
    html = client.get("/lists/").content.decode()
    assert "Party" in html and "0 items" in html


# ---- ownership (Review Focus 5) ------------------------------------------------------------

ROUTES = [
    ("get", "{b}/"),
    ("get", "{b}/delete"),
    ("post", "{b}/rename"),
    ("post", "{b}/delete"),
    ("post", "{b}/items/{i}/target"),
    ("post", "{b}/items/{i}/remove"),
    ("post", "{b}/items/{i}/pin"),
    ("post", "{b}/items/{i}/unpin"),
]
BODY = {"name": "x", "amount": "1", "unit": "gal", "product": "1", "confirm": "yes"}


def user(email):
    return get_user_model().objects.create_user(
        username=email, email=email, password="pw-12345-pw"
    )


def owned_by_session(client):
    spec = seed(client, milk_item(1))
    return spec


def owned_by_user(u):
    spec = make_list(milk_item(1))
    AccountListStore(u).save(spec)
    return spec


def assert_all_404(client, spec):
    for method, pattern in ROUTES:
        path = pattern.format(b=f"/lists/{spec.id}", i=spec.items[0].id)
        resp = getattr(client, method)(path, BODY if method == "post" else {})
        assert resp.status_code == 404, (method, path)
    # the data is untouched
    return spec


@pytest.mark.django_db
def test_other_owners_list_404(client, milk_offers):
    a_spec = owned_by_session(client)
    # anonymous B
    assert_all_404(Client(), a_spec)
    # signed-in C
    c = Client()
    c.force_login(user("c@example.com"))
    assert_all_404(c, a_spec)
    assert SessionListStore(client.session).get(a_spec.id) is not None

    # a user-owned list: another user and an anonymous client
    owner = user("d@example.com")
    u_spec = owned_by_user(owner)
    other = Client()
    other.force_login(user("e@example.com"))
    assert_all_404(other, u_spec)
    assert_all_404(Client(), u_spec)
    assert AccountListStore(owner).get(u_spec.id) is not None
    # and the owner can still reach it
    mine = Client()
    mine.force_login(owner)
    assert mine.get(f"/lists/{u_spec.id}/").status_code == 200


def test_add_to_other_owners_list_404(client, milk_offers):
    spec = owned_by_session(client)
    resp = Client().post(
        "/lists/add",
        {"category": "dairy/milk", "amount": "1", "unit": "gal", "list": spec.id},
    )
    assert resp.status_code == 404
    assert len(SessionListStore(client.session).get(spec.id).items) == 1


def test_unknown_item_404(client, milk_offers):
    spec = seed(client, milk_item(1))
    assert client.post(f"/lists/{spec.id}/items/{'f' * 32}/remove").status_code == 404


def test_post_requires_csrf(milk_offers):
    c = Client(enforce_csrf_checks=True)
    spec = seed(c, milk_item(1))
    base = f"/lists/{spec.id}"
    for path in (
        "/lists/new",
        "/lists/add",
        f"{base}/rename",
        f"{base}/delete",
        f"{base}/items/{spec.items[0].id}/remove",
    ):
        assert c.post(path, {"name": "x"}).status_code == 403, path
    assert SessionListStore(c.session).get(spec.id) is not None


# ---- fetch enqueue -------------------------------------------------------------------------


def generic_item(n=5, query="fizz soda"):
    return ItemSpec(
        id=f"{n:032x}",
        category=None,
        filters={},
        target_measure="volume",
        target_amount=Decimal(2000),
        kind="generic",
        query=query,
    )


@pytest.fixture
def sources(monkeypatch):
    info = SourceInfo(name="src", budget_keys=(), refresh_after=timedelta(hours=6))
    monkeypatch.setattr(rs, "live_source_info", lambda **kw: {"src": info})


def test_list_view_enqueues_list_jobs(client, db, sources):
    spec = seed(
        client, generic_item(1, "fizz soda"), generic_item(2, "oat milk"), milk_item(3)
    )
    assert client.get(detail_url(spec)).status_code == 200
    assert set(FetchJob.objects.values_list("phrase", "origin")) == {
        ("fizz soda", "list"),
        ("oat milk", "list"),
    }


def test_list_view_does_not_enqueue_for_htmx_bots_or_post(client, db, sources):
    spec = seed(client, generic_item(1, "fizz soda"))
    client.get(detail_url(spec), HTTP_HX_REQUEST="true")
    client.get(
        detail_url(spec),
        HTTP_USER_AGENT="Googlebot/2.1 (+http://www.google.com/bot.html)",
    )
    client.head(detail_url(spec))
    assert FetchJob.objects.count() == 0


# ---- query count ---------------------------------------------------------------------------


def test_list_page_query_count_is_bounded(client, milk, load):
    stores = ["walmart", "target", "amazon", "kroger", "costco"]
    offers = [
        raw(
            title=f"Brand{n} 2% Milk, {size} fl oz",
            price=f"{2 + n * 0.1 + size / 64:.2f}",
            item=f"{s}-{n}-{size}",
            retailer=s,
            brand=f"Brand{n}",
        )
        for s in stores
        for n in range(3)
        for size in (64, 128)
    ]
    load(milk, offers)
    for n in range(5):
        make_generic_offer(
            f"Fizz Soda {n}", "2", dim="volume", amount=1000 + n, retailer=stores[n % 5]
        )
    imperial(client)
    items = [milk_item(n, fat="2", amount=GAL + n) for n in range(1, 11)]
    items += [generic_item(100 + n, "fizz soda") for n in range(10)]
    spec = seed(client, *items)
    with CaptureQueriesContext(connection) as ctx:
        resp = client.get(detail_url(spec))
    assert resp.status_code == 200
    # one query per curated or generic item plus a fixed handful; none per offer or per store
    assert len(ctx) <= 2 * len(items) + 15, [q["sql"][:80] for q in ctx]
    few = seed(client, items[0], generic_item(200, "fizz soda"))
    with CaptureQueriesContext(connection) as small:
        client.get(detail_url(few))
    assert (len(ctx) - len(small)) <= 2 * (len(items) - 2) + 2


# ---- hosts ---------------------------------------------------------------------------------


def test_lists_routes_served_on_apex(client, settings, db):
    # Launch (#77): the apex is the public app; lists answer exactly as on beta.
    settings.PUBLIC_HOST = "unitpricer.com"
    settings.APP_HOST = "beta.unitpricer.com"
    settings.ALLOWED_HOSTS = ["unitpricer.com", "beta.unitpricer.com"]
    settings.SECURE_SSL_REDIRECT = False
    for method, path in [
        ("get", "/lists/"),
        ("get", "/lists/add?category=dairy%2Fmilk"),
        ("post", "/lists/new"),
        ("get", f"/lists/{'a' * 32}/"),
    ]:
        apex = getattr(client, method)(path, HTTP_HOST="unitpricer.com")
        beta = getattr(client, method)(path, HTTP_HOST="beta.unitpricer.com")
        assert apex.status_code == beta.status_code, path
    assert client.get("/lists/", HTTP_HOST="unitpricer.com").status_code == 200


# ---- final review fixes --------------------------------------------------------------------


def test_detail_bogus_store_params_fall_back_to_prefs_fast(client, milk_offers):
    import time

    spec = two_item_list(client)
    query = "&".join(f"store=bogus{n}" for n in range(500))
    started = time.monotonic()
    resp = client.get(detail_url(spec) + "?" + query)
    assert time.monotonic() - started < 5
    html = resp.content.decode()
    assert resp.status_code == 200
    # Unknown codes are dropped; nothing left means the saved stores (all of them).
    assert "at Walmart" in html and "Target" in html
    assert 'value="bogus' not in html  # not echoed into the split form


def test_detail_store_params_intersect_live_retailers(client, milk_offers):
    spec = two_item_list(client)
    html = client.get(detail_url(spec) + "?store=target&store=bogus").content.decode()
    assert "at Target" in html and "at Walmart" not in html
    assert 'name="store" value="target"' in html and 'value="bogus' not in html


def test_cheapest_split_caps_store_count():
    import time

    from lists.basket import MAX_SPLIT_STORES, cheapest_split

    stores = [f"s{n:02d}" for n in range(40)]
    costs = {
        f"i{i}": {s: Decimal(1 + (i * 7 + n * 13) % 17) for n, s in enumerate(stores)}
        for i in range(30)
    }
    started = time.monotonic()
    split = cheapest_split(costs, stores, None)
    assert time.monotonic() - started < 5
    assert split is not None and len(split.stores) <= MAX_SPLIT_STORES


def test_chain_only_prefs_still_resolve_live_offers(client, milk_offers):
    session = client.session
    session["prefs"] = {"unit_system": "imperial", "stores": ["op-kroger"]}
    session.save()
    spec = seed(client, milk_item(1))
    html = client.get(detail_url(spec)).content.decode()
    assert "at Walmart" in html
    assert "No current prices match" not in html


def test_resolver_chain_only_stores_mean_all_live(milk_offers):
    from lists.resolve import resolve_list

    res = resolve_list(make_list(milk_item(1)), frozenset({"op-kroger"}), NOW)
    assert set(res.stores) == {"walmart", "target"}
    assert res.items[0].best is not None


def test_item_actions_have_per_item_labels(client, milk_offers):
    imperial(client)
    spec = seed(client, milk_item(1))
    html = client.get(detail_url(spec)).content.decode()
    assert 'aria-label="Pin Milk · fat 2"' in html
    assert 'aria-label="Change amount for Milk · fat 2"' in html
    assert 'aria-label="Remove Milk · fat 2"' in html


def test_delete_list_needs_confirmation(client, milk_offers):
    spec = seed(client, milk_item(1))
    base = f"/lists/{spec.id}"
    html = client.get(base + "/").content.decode()
    assert f'href="{base}/delete"' in html
    page = client.get(f"{base}/delete")
    assert page.status_code == 200
    assert "Delete &ldquo;Groceries&rdquo;?" in page.content.decode()
    unconfirmed = client.post(f"{base}/delete")
    assert unconfirmed.status_code == 302 and unconfirmed["Location"] == f"{base}/delete"
    assert SessionListStore(client.session).get(spec.id) is not None
    done = client.post(f"{base}/delete", {"confirm": "yes"})
    assert done.status_code == 302 and done["Location"] == "/lists/"
    assert SessionListStore(client.session).get(spec.id) is None


def test_add_to_deleted_list_rerenders_form(client, milk_offers):
    imperial(client)
    gone = seed(client, milk_item(1), name="Old")
    keep = seed(client, milk_item(2), name="Keep")
    client.post(f"/lists/{gone.id}/delete", {"confirm": "yes"})
    form = {"category": "dairy/milk", "fat": "2", "amount": "1", "unit": "gal"}
    resp = client.post("/lists/add", {**form, "list": gone.id})
    html = resp.content.decode()
    assert resp.status_code == 404  # same as any unknown id (ids are not capabilities)
    assert "That list no longer exists" in html
    assert 'name="amount" inputmode="decimal" value="1"' in html
    assert '<option value="new" selected>' in html and "Keep" in html
    assert [len(s.items) for s in SessionListStore(client.session).lists()] == [1]
    assert SessionListStore(client.session).get(keep.id).items == keep.items


def test_add_unknown_and_foreign_ids_look_the_same(client, milk_offers):
    foreign = owned_by_session(client)
    form = {"category": "dairy/milk", "amount": "1", "unit": "gal"}
    other = Client()
    a = other.post("/lists/add", {**form, "list": foreign.id})
    b = other.post("/lists/add", {**form, "list": "f" * 32})
    strip = __import__("re").compile(rb'name="csrfmiddlewaretoken" value="[^"]+"')
    assert a.status_code == b.status_code == 404
    assert strip.sub(b"", a.content) == strip.sub(b"", b.content)
    assert "Groceries" not in a.content.decode()
    assert len(SessionListStore(client.session).get(foreign.id).items) == 1


def test_item_posts_require_csrf(milk_offers):
    c = Client(enforce_csrf_checks=True)
    spec = seed(c, milk_item(1))
    base = f"/lists/{spec.id}/items/{spec.items[0].id}"
    pid = Product.objects.get(offers__retailer_item_id="b128").pk
    for path, body in (
        (f"{base}/target", {"amount": "2", "unit": "gal"}),
        (f"{base}/pin", {"product": pid}),
        (f"{base}/unpin", {}),
    ):
        assert c.post(path, body).status_code == 403, path
    assert SessionListStore(c.session).get(spec.id) == spec


def test_queue_fetches_one_query_and_stops_at_cap(db, sources, monkeypatch):
    spec = make_list(*[generic_item(n, f"phrase {n:02d}") for n in range(1, 30)])
    FetchJob.objects.create(
        source="src",
        phrase="phrase 01",
        origin="search",
        status=FetchJob.Status.SUCCEEDED,
        not_before=NOW,
        finished_at=NOW - timedelta(hours=1),
    )
    calls = []
    monkeypatch.setattr(
        rs, "enqueue", lambda p, o, n, s, *, client_key: calls.append(p) or []
    )
    with CaptureQueriesContext(connection) as ctx:
        assert rs.queue_fetches(spec, NOW, None) == rs.MAX_FETCH_PHRASES
    assert len(ctx) == 1, [q["sql"][:120] for q in ctx]
    assert calls == [f"phrase {n:02d}" for n in range(2, 12)]


def test_list_view_passes_raw_client_ip_as_key(client, db, sources, monkeypatch):
    seen = []
    monkeypatch.setattr(
        rs, "enqueue", lambda p, o, n, s, *, client_key: seen.append(client_key) or []
    )
    spec = seed(client, generic_item(1))
    client.get(detail_url(spec), REMOTE_ADDR="203.0.113.9")
    assert seen == ["203.0.113.9"]  # allow() salts and hashes it, as for search
