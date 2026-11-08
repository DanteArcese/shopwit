from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.db import IntegrityError, transaction

from lists import spec as sp
from lists.models import ListItem, ShoppingList
from lists.spec import ItemSpec, ListLimitError
from lists.store import AccountListStore, SessionListStore, store_for

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def curated(n=1, pinned=None):
    return ItemSpec(
        id=f"{n:032x}",
        category="dairy/milk",
        filters={"fat": "2"},
        target_measure="volume_ml",
        target_amount=Decimal("3785.412"),
        pinned_product=pinned,
    )


def generic(n=50):
    return ItemSpec(
        id=f"{n:032x}",
        category=None,
        filters={},
        target_measure="mass",
        target_amount=Decimal("453.592"),
        kind="generic",
        query="rolled oats",
        facets={"organic": ("true",), "size": ("1", "2")},
        unit_system="imperial",
    )


def two_item_list(now=NOW, pinned=None):
    s = sp.new_list("Weekly", now)
    s = sp.with_item(s, curated(1, pinned), now)
    return sp.with_item(s, generic(), now)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("a@example.com", "a@example.com")


def test_json_round_trip(db):
    from catalog.models import Category, Product

    cat = Category.objects.create(slug="x", name="X", dimension="mass", comparison_default="g")
    p = Product.objects.create(category=cat, title="t")
    s = two_item_list(pinned=p.id)
    assert sp.from_json(sp.to_json(s)) == s
    assert s.items[0].pinned_product == p.id


def test_item_spec_generic_round_trip():
    s = two_item_list()
    back = sp.from_json(sp.to_json(s))
    g = back.items[1]
    assert (g.kind, g.query, g.category, g.unit_system) == ("generic", "rolled oats", None, "imperial")
    assert g.facets == {"organic": ("true",), "size": ("1", "2")}
    assert g.target_measure == "mass"


def test_legacy_json_reads_as_curated():
    data = sp.to_json(sp.with_item(sp.new_list("L", NOW), curated(), NOW))
    for k in ("kind", "query", "facets", "unit_system"):
        del data["items"][0][k]
    item = sp.from_json(data).items[0]
    assert item.kind == "curated"
    assert item.unit_system == "metric"
    assert item.category == "dairy/milk"


def test_from_json_tolerates_garbage():
    assert sp.from_json(None) is None
    assert sp.from_json({"id": 1}) is None
    data = sp.to_json(two_item_list())
    data["items"].insert(0, {"id": "bad", "category": "x"})
    data["items"].append({**data["items"][1], "kind": "generic", "query": ""})
    data["items"].append({**data["items"][1], "target_amount": "nan"})
    back = sp.from_json(data)
    assert [i.kind for i in back.items] == ["curated", "generic"]


def test_session_store_round_trip_and_order():
    store = SessionListStore(SessionStore())
    a = sp.new_list("A", NOW)
    b = sp.new_list("B", NOW + timedelta(minutes=1))
    store.save(a)
    store.save(b)
    assert [s.name for s in store.lists()] == ["B", "A"]
    store.save(sp.renamed(a, "A2", NOW + timedelta(minutes=2)))
    assert [s.name for s in store.lists()] == ["A2", "B"]
    assert store.get(b.id) == b
    store.delete(b.id)
    assert store.get(b.id) is None


def test_session_store_read_does_not_modify_session():
    s = SessionStore()
    store = SessionListStore(s)
    store.lists()
    store.get("x")
    assert not s.modified


def test_account_store_round_trip(user):
    s = two_item_list()
    AccountListStore(user).save(s)
    back = AccountListStore(user).get(s.id)
    assert back == s
    assert [i.id for i in back.items] == [i.id for i in s.items]
    assert AccountListStore(user).lists() == [s]


def test_account_store_replaces_removed_items(user):
    store = AccountListStore(user)
    s = two_item_list()
    store.save(s)
    store.save(sp.without_item(s, s.items[0].id, NOW))
    assert ListItem.objects.count() == 1
    assert store.get(s.id).items == (s.items[1],)


def test_account_store_reorders_and_updates_in_place(user):
    store = AccountListStore(user)
    s = two_item_list()
    store.save(s)
    ids = list(ListItem.objects.values_list("id", flat=True))
    flipped = sp.ListSpec(s.id, "Renamed", tuple(reversed(s.items)), NOW)
    store.save(flipped)
    assert store.get(s.id) == flipped
    assert sorted(ListItem.objects.values_list("id", flat=True)) == sorted(ids)


def test_account_store_scoped_to_user(user):
    other = get_user_model().objects.create_user("b@example.com", "b@example.com")
    s = two_item_list()
    AccountListStore(user).save(s)
    assert AccountListStore(other).get(s.id) is None
    assert AccountListStore(other).lists() == []
    AccountListStore(other).delete(s.id)
    assert AccountListStore(user).get(s.id) is not None


def test_limits(user):
    for store in (SessionListStore(SessionStore()), AccountListStore(user)):
        for i in range(sp.MAX_LISTS):
            store.save(sp.new_list(f"L{i}", NOW))
        with pytest.raises(ListLimitError):
            store.save(sp.new_list("one too many", NOW))
        existing = store.lists()[0]
        store.save(sp.renamed(existing, "still fine", NOW))
    s = sp.new_list("big", NOW)
    for i in range(sp.MAX_ITEMS):
        s = sp.with_item(s, curated(i + 1), NOW)
    with pytest.raises(ListLimitError):
        sp.with_item(s, curated(999), NOW)


def test_name_defaults_and_trims():
    assert sp.new_list("   ", NOW).name == sp.DEFAULT_NAME
    assert sp.new_list("", NOW).name == "My list"
    assert len(sp.new_list("x" * 200, NOW).name) == sp.MAX_NAME
    assert sp.new_list("  hi  ", NOW).name == "hi"


def test_listitem_kind_check_constraint(user):
    row = ShoppingList.objects.create(user=user, uid="l", name="L")
    base = dict(shopping_list=row, position=0, target_measure="mass", target_amount=1)

    def bad(**kw):
        with pytest.raises(IntegrityError), transaction.atomic():
            ListItem.objects.create(uid=kw.pop("uid"), **base, **kw)

    bad(uid="1", kind="curated", category_slug=None)
    bad(uid="2", kind="generic", query="")
    bad(uid="3", kind="generic", query="oats", unit_system="cubits")
    bad(uid="4", kind="other", category_slug="x")
    ListItem.objects.create(uid="5", kind="curated", category_slug="x", **base)
    ListItem.objects.create(uid="6", kind="generic", query="oats", **base)


def test_store_for_picks_backend(user, rf):
    req = rf.get("/")
    req.session = SessionStore()
    req.user = user
    assert isinstance(store_for(req), AccountListStore)
    from django.contrib.auth.models import AnonymousUser

    req.user = AnonymousUser()
    assert isinstance(store_for(req), SessionListStore)
