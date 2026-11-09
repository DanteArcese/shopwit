import re
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client

from accounts.merge import MergeResult, merge_session_into_account
from accounts.models import UserSettings
from lists.models import ListItem, ShoppingList
from lists.spec import MAX_ITEMS, MAX_LISTS, ItemSpec, ListSpec, new_id
from lists.store import AccountListStore, SessionListStore
from tests.catalog.helpers import NOW

User = get_user_model()
PASSWORD = "correct-horse-battery-9"
LINK = re.compile(r"https://\S+/account/magic/([A-Za-z0-9_-]+)")


def item(n, **kw):
    base = dict(
        id=f"{n:032x}",
        category="dairy/milk",
        filters={"fat": "2"},
        target_measure="volume_ml",
        target_amount=Decimal("3785.412"),
    )
    return ItemSpec(**{**base, **kw})


def generic_item(n):
    return ItemSpec(
        id=f"{n:032x}",
        category=None,
        filters={},
        target_measure="volume",
        target_amount=Decimal("2.000"),
        kind="generic",
        query="oat milk",
        facets={"brand": ("acme",)},
        unit_system="imperial",
    )


def lst(name, *items, list_id=None):
    return ListSpec(id=list_id or new_id(), name=name, items=tuple(items), updated_at=NOW)


def user(email="u@example.com"):
    return User.objects.create_user(username=email, email=email, password=PASSWORD)


def session_with(*specs, prefs=None):
    session = {}
    store = SessionListStore(session)
    for s in specs:
        store.save(s)
    if prefs is not None:
        session["prefs"] = prefs
    return session


def counts():
    return ShoppingList.objects.count(), ListItem.objects.count()


@pytest.mark.django_db
def test_merge_moves_session_lists(client):
    spec = lst("Weekly", item(1))
    session = client.session
    SessionListStore(session).save(spec)
    session.save()
    u = user()
    r = client.post("/account/magic-link", {"email": u.email})
    assert r.status_code == 200
    token = LINK.search(mail.outbox[-1].body).group(1)
    assert client.post(f"/account/magic/{token}").status_code == 302
    assert "lists" not in client.session
    page = client.get("/lists/")
    assert "Weekly" in page.content.decode()
    assert AccountListStore(u).get(spec.id) is not None


@pytest.mark.django_db
def test_merge_idempotent():
    u = user()
    specs = [lst("A", item(1), item(2)), lst("B", generic_item(3))]
    first = merge_session_into_account(session_with(*specs), u)
    assert first == MergeResult(lists_added=2, items_added=3, lists_skipped=0)
    after_first = counts()
    assert after_first == (2, 3)
    again = merge_session_into_account(session_with(*specs), u)
    assert again == MergeResult(0, 0, 0)
    assert counts() == after_first


@pytest.mark.django_db
def test_merge_keeps_existing_account_lists():
    u = user()
    mine = lst("Mine", item(1), item(2))
    AccountListStore(u).save(mine)
    before = AccountListStore(u).get(mine.id)
    merge_session_into_account(session_with(lst("Phone", item(3))), u)
    assert AccountListStore(u).get(mine.id) == before
    assert {s.name for s in AccountListStore(u).lists()} == {"Mine", "Phone"}


@pytest.mark.django_db
def test_merge_same_uid_adds_only_new_items():
    u = user()
    shared = new_id()
    AccountListStore(u).save(lst("Acct name", item(1), item(2), list_id=shared))
    result = merge_session_into_account(
        session_with(lst("Session name", item(2), item(3), list_id=shared)), u
    )
    assert result == MergeResult(lists_added=0, items_added=1, lists_skipped=0)
    merged = AccountListStore(u).get(shared)
    assert merged.name == "Acct name"
    assert [i.id for i in merged.items] == [item(1).id, item(2).id, item(3).id]


@pytest.mark.django_db
def test_merge_respects_item_limit():
    u = user()
    shared = new_id()
    AccountListStore(u).save(lst("Full", *[item(n) for n in range(1, MAX_ITEMS)], list_id=shared))
    result = merge_session_into_account(
        session_with(lst("S", item(900), item(901), list_id=shared)), u
    )
    assert result.items_added == 1
    assert result.lists_skipped == 1
    assert len(AccountListStore(u).get(shared).items) == MAX_ITEMS


@pytest.mark.django_db
def test_merge_preserves_generic_item_unit_system():
    u = user()
    merge_session_into_account(session_with(lst("G", generic_item(1))), u)
    (got,) = AccountListStore(u).lists()[0].items
    assert got.kind == "generic" and got.unit_system == "imperial"
    assert got.facets == {"brand": ("acme",)}


@pytest.mark.django_db
def test_merge_respects_limits(client):
    u = user()
    store = AccountListStore(u)
    for n in range(MAX_LISTS - 1):
        store.save(lst(f"L{n}", item(n + 1)))
    session = client.session
    for n in range(3):
        SessionListStore(session).save(lst(f"S{n}", item(100 + n)))
    session.save()
    result = merge_session_into_account(client.session, u)
    assert (result.lists_added, result.lists_skipped) == (1, 2)
    assert ShoppingList.objects.filter(user=u).count() == MAX_LISTS


@pytest.mark.django_db
def test_limit_warning_shown_after_sign_in():
    u = user()
    store = AccountListStore(u)
    for n in range(MAX_LISTS):
        store.save(lst(f"L{n}", item(n + 1)))
    c = Client()
    session = c.session
    SessionListStore(session).save(lst("Extra", item(500)))
    session.save()
    r = c.post("/account/sign-in", {"email": u.email, "password": PASSWORD}, follow=True)
    html = r.content.decode().replace("&#x27;", "'")
    assert "Some lists from this device didn't fit in your account (20 lists, 100 items each)." in html
    assert ShoppingList.objects.filter(user=u).count() == MAX_LISTS


@pytest.mark.django_db
def test_password_sign_in_also_merges():
    u = user()
    c = Client()
    session = c.session
    spec = lst("Pw", item(1))
    SessionListStore(session).save(spec)
    session.save()
    r = c.post("/account/sign-in", {"email": u.email, "password": PASSWORD})
    assert r.status_code == 302
    assert AccountListStore(u).get(spec.id) is not None
    assert "lists" not in c.session


@pytest.mark.django_db
def test_merge_pops_session_keys_even_with_nothing():
    u = user()
    session = {"lists": [], "prefs": {"unit_system": "metric", "stores": None}}
    merge_session_into_account(session, u)
    assert "lists" not in session and "prefs" not in session


@pytest.mark.django_db
def test_merge_without_session_prefs_creates_no_settings_row():
    u = user()
    merge_session_into_account(session_with(lst("A", item(1))), u)
    assert not UserSettings.objects.exists()




@pytest.mark.django_db
def test_merge_drops_dangling_pin():
    u = user()
    spec = lst("Pins", item(1, pinned_product=987654321))
    merge_session_into_account(session_with(spec), u)
    (got,) = AccountListStore(u).get(spec.id).items
    assert got.pinned_product is None


@pytest.mark.django_db
def test_merge_failure_keeps_sign_in(monkeypatch, caplog):
    import logging

    u = user()

    def boom(session, user):
        raise RuntimeError("secret detail u@example.com")

    monkeypatch.setattr("accounts.merge.merge_session_into_account", boom)
    c = Client()
    with caplog.at_level(logging.DEBUG):
        r = c.post("/account/sign-in", {"email": u.email, "password": PASSWORD})
    assert r.status_code == 302
    assert c.session["_auth_user_id"] == str(u.pk)
    assert "RuntimeError" in caplog.text
    assert "secret detail" not in caplog.text and "u@example.com" not in caplog.text
