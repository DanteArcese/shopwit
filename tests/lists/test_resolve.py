from datetime import timedelta
from decimal import Decimal

import pytest

from catalog.fetch import enqueue as real_enqueue
from catalog.models import FetchJob, Offer
from connectors.live import SourceInfo
from lists import resolve as rs
from lists.resolve import resolve_item, resolve_list
from lists.spec import ItemSpec, ListSpec
from tests.catalog.helpers import NOW, raw
from tests.catalog.test_generic_match import make_generic_offer

GAL_ML = Decimal("3785.412")
BOTH = frozenset({"fake", "other"})


def item(
    n=1,
    filters=None,
    amount=GAL_ML,
    pinned=None,
    category="dairy/milk",
    measure="volume_ml",
):
    return ItemSpec(
        id=f"{n:032x}",
        category=category,
        filters=filters if filters is not None else {},
        target_measure=measure,
        target_amount=amount,
        pinned_product=pinned,
    )


def gitem(
    n=90,
    query="fizz soda",
    dim="volume",
    amount=Decimal(2000),
    facets=None,
    unit_system="metric",
):
    return ItemSpec(
        id=f"{n:032x}",
        category=None,
        filters={},
        target_measure=dim,
        target_amount=amount,
        kind="generic",
        query=query,
        facets=facets or {},
        unit_system=unit_system,
    )


def lst(*items):
    return ListSpec(id="l" * 32, name="L", items=tuple(items), updated_at=NOW)


def offer(title, retailer="fake"):
    return Offer.objects.get(title=title, retailer=retailer)


@pytest.fixture(autouse=True)
def _no_live_sources(monkeypatch):
    monkeypatch.setattr(rs, "live_source_info", lambda **kw: {})


def test_cheapest_qualifying_offer_per_item(milk, load):
    load(
        milk,
        [
            raw(title="Acme 2% Milk, 1 gal", price="4.00", item="a", brand="Acme"),
            raw(title="Bravo 2% Milk, 1 gal", price="3.50", item="b", brand="Bravo"),
            raw(title="Cargo Whole Milk, 1 gal", price="2.00", item="c", brand="Cargo"),
        ],
    )
    res = resolve_item(item(filters={"fat": "2"}), BOTH, NOW)
    assert res.reason is None
    assert res.best.offer.title == "Bravo 2% Milk, 1 gal"
    assert res.best.packages == 1 and res.best.total_price == Decimal("3.50")
    assert res.category == milk


def test_unknown_excluded_under_filter(milk, load):
    load(
        milk,
        [
            raw(title="Acme 2% Milk, 1 gal", price="4.00", item="a", brand="Acme"),
            raw(title="Mystery Milk, 1 gal", price="1.00", item="m", brand="Mystery"),
        ],
    )
    res = resolve_item(item(filters={"fat": "2"}), BOTH, NOW)
    assert res.best.offer.title == "Acme 2% Milk, 1 gal"
    assert all(f.offer.title != "Mystery Milk, 1 gal" for f in res.by_store.values())


def test_stale_offer_excluded_from_totals(milk, load):
    load(milk, [raw(title="Acme 2% Milk, 1 gal", price="4.00", item="a", brand="Acme")])
    load(
        milk,
        [
            raw(
                title="Cheap 2% Milk, 1 gal",
                price="1.00",
                item="c",
                retailer="other",
                brand="Cheap",
            )
        ],
        now=NOW - timedelta(hours=25),
    )
    res = resolve_item(item(filters={"fat": "2"}), BOTH, NOW)
    assert set(res.by_store) == {"fake"}
    assert res.best.offer.title == "Acme 2% Milk, 1 gal"

    out = resolve_list(lst(item(filters={"fat": "2"})), BOTH, NOW)
    by_store = {t.store: t for t in out.totals}
    assert by_store["other"].missing == (f"{1:032x}",) and by_store["other"].total == 0
    assert out.split.total == Decimal("4.00") and out.split.stores == ("fake",)
    assert out.single.store == "fake"
    assert out.oldest_price_at == NOW


def test_quantity_fit_alternatives(milk, load):
    load(
        milk,
        [
            raw(title="Acme 2% Milk, 64 fl oz", price="3.00", item="h", brand="Acme"),
            raw(title="Acme 2% Milk, 128 fl oz", price="5.48", item="g", brand="Acme"),
        ],
    )
    res = resolve_item(item(), BOTH, NOW)
    assert res.best.packages == 1 and res.best.total_price == Decimal("5.48")
    assert res.alternatives[0].packages == 2
    assert res.alternatives[0].total_price == Decimal("6.00")
    assert len(res.alternatives) == 1


def test_alternatives_at_most_two_one_per_size(milk, load):
    load(
        milk,
        [
            raw(title="A 2% Milk, 1 gal", price="5.00", item="1", brand="A"),
            raw(title="B 2% Milk, 64 fl oz", price="3.00", item="2", brand="B"),
            raw(title="C 2% Milk, 64 fl oz", price="2.90", item="3", brand="C"),
            raw(title="D 2% Milk, 32 fl oz", price="1.60", item="4", brand="D"),
            raw(title="E 2% Milk, 16 fl oz", price="0.85", item="5", brand="E"),
            raw(
                title="F 2% Milk, 128 fl oz",
                price="9.00",
                item="6",
                brand="F",
                retailer="other",
            ),
        ],
    )
    res = resolve_item(item(), BOTH, NOW)
    assert res.best.offer.title == "A 2% Milk, 1 gal"
    alts = res.alternatives
    assert [a.offer.title for a in alts] == [
        "C 2% Milk, 64 fl oz",
        "D 2% Milk, 32 fl oz",
    ]
    assert all(a.offer.retailer == "fake" for a in alts)


def test_pinned_product(milk, load):
    load(
        milk,
        [
            raw(title="Acme 2% Milk, 1 gal", price="4.00", item="a", brand="Acme"),
            raw(title="Bravo 2% Milk, 1 gal", price="3.00", item="b", brand="Bravo"),
        ],
    )
    load(
        milk,
        [raw(title="Old 2% Milk, 1 gal", price="1.00", item="o", brand="Old")],
        now=NOW - timedelta(hours=25),
    )
    pin = offer("Acme 2% Milk, 1 gal").product_id
    res = resolve_item(item(pinned=pin, filters={"fat": "2"}), BOTH, NOW)
    assert res.best.offer.title == "Acme 2% Milk, 1 gal"
    assert set(res.by_store) == {"fake"}

    stale = offer("Old 2% Milk, 1 gal").product_id
    res = resolve_item(item(pinned=stale), BOTH, NOW)
    assert res.best is None
    assert res.reason == "The pinned product has no current price at your stores"


def test_reasons(milk, load):
    load(
        milk,
        [
            raw(title="Acme 2% Milk, 1 gal", price="4.00", item="a", brand="Acme"),
            raw(title="Plain 2% Milk", price="2.00", item="p", brand="Plain"),
        ],
    )
    assert resolve_item(item(category="no/such"), BOTH, NOW).reason == (
        "This category is no longer available"
    )
    res = resolve_item(item(filters={"fat": "7", "colour": "red"}), BOTH, NOW)
    assert res.reason == "Filter no longer valid: colour, fat" and res.best is None
    assert resolve_item(item(), frozenset({"nobody"}), NOW).reason == (
        "No current prices match at your stores"
    )
    plain = offer("Plain 2% Milk").product_id
    assert (
        resolve_item(item(pinned=plain), BOTH, NOW).reason
        == "Size unknown for every match"
    )
    assert (
        resolve_item(item(amount=GAL_ML * 30), BOTH, NOW).reason
        == "Needs more than 24 packages"
    )

    milk.active = False
    milk.save()
    res = resolve_item(item(), BOTH, NOW)
    assert res.reason == "This category is no longer available" and res.category is None


def test_store_selection_limits_candidates(milk, load):
    load(
        milk,
        [
            raw(title="Acme 2% Milk, 1 gal", price="1.00", item="a", brand="Acme"),
            raw(
                title="Bravo 2% Milk, 1 gal",
                price="3.00",
                item="b",
                retailer="other",
                brand="Bravo",
            ),
        ],
    )
    res = resolve_item(item(), frozenset({"other"}), NOW)
    assert set(res.by_store) == {"other"}
    assert res.best.offer.retailer == "other"


def _two_items(milk, load):
    load(
        milk,
        [
            raw(title="Acme 2% Milk, 1 gal", price="3.00", item="a", brand="Acme"),
            raw(title="Acme Whole Milk, 1 gal", price="5.00", item="w", brand="Acme"),
            raw(
                title="Bravo 2% Milk, 1 gal",
                price="4.00",
                item="b",
                retailer="other",
                brand="Bravo",
            ),
            raw(
                title="Bravo Whole Milk, 1 gal",
                price="3.00",
                item="v",
                retailer="other",
                brand="Bravo",
            ),
        ],
    )
    return lst(item(1, {"fat": "2"}), item(2, {"fat": "whole"}))


def test_single_and_split(milk, load):
    out = resolve_list(_two_items(milk, load), BOTH, NOW)
    assert not out.mixed_currency and out.currency == "USD"
    assert {t.store: t.total for t in out.totals} == {
        "fake": Decimal("8.00"),
        "other": Decimal("7.00"),
    }
    assert out.single.store == "other" and out.single.total == Decimal("7.00")
    assert out.split.total == Decimal("6.00") < out.single.total
    assert set(out.split.stores) == {"fake", "other"}
    assert out.unresolved == ()
    assert out.max_split_stores == 2


def test_split_respects_max_stores(milk, load):
    out = resolve_list(_two_items(milk, load), BOTH, NOW, max_split_stores=1)
    assert out.split.total == Decimal("7.00") and out.split.stores == ("other",)


def test_stores_none_means_live_retailers_sorted_by_name(milk, load):
    load(
        milk,
        [
            raw(
                title="Acme 2% Milk, 1 gal",
                price="3.00",
                item="a",
                retailer="walmart",
                brand="Acme",
            ),
            raw(
                title="Bravo 2% Milk, 1 gal",
                price="3.00",
                item="b",
                retailer="amazon",
                brand="Bravo",
            ),
        ],
    )
    Offer.objects.create(
        product=offer("Acme 2% Milk, 1 gal", "walmart").product,
        retailer="op-x",
        retailer_item_id="x",
        price=Decimal("0.50"),
        currency="USD",
        url="https://example.com/",
        title="seen",
        kind="observed",
        observed_on=NOW.date(),
        last_seen_at=NOW,
    )
    out = resolve_list(lst(item()), None, NOW)
    assert out.stores == (
        "amazon",
        "walmart",
    )  # observed chains never enter list totals
    assert out.items[0].best.offer.kind == "live"


def test_unresolved_items_excluded_from_costs(milk, load):
    load(milk, [raw(title="Acme 2% Milk, 1 gal", price="3.00", item="a", brand="Acme")])
    good, bad = item(1), item(2, {"fat": "7"})
    out = resolve_list(lst(good, bad), BOTH, NOW)
    assert out.unresolved == (bad.id,)
    assert out.split is not None and out.split.total == Decimal("3.00")
    assert out.single.store == "fake"
    assert all(bad.id not in t.missing for t in out.totals)


def test_nothing_resolved_has_no_totals(milk, load):
    out = resolve_list(lst(item(1, {"fat": "7"})), BOTH, NOW)
    assert out.totals == [] and out.single is None and out.split is None
    assert out.currency is None and out.oldest_price_at is None
    out = resolve_list(lst(), BOTH, NOW)
    assert out.totals == [] and out.single is None and out.split is None


def test_no_full_coverage_single_is_none(milk, load):
    load(
        milk,
        [
            raw(title="Acme 2% Milk, 1 gal", price="3.00", item="a", brand="Acme"),
            raw(
                title="Bravo Whole Milk, 1 gal",
                price="3.00",
                item="v",
                retailer="other",
                brand="Bravo",
            ),
        ],
    )
    out = resolve_list(lst(item(1, {"fat": "2"}), item(2, {"fat": "whole"})), BOTH, NOW)
    assert out.single is None
    assert out.split.total == Decimal("6.00")


def test_ties_break_by_product_then_offer_id(milk, load):
    load(
        milk,
        [
            raw(title="Zed 2% Milk, 1 gal", price="3.00", item="z", brand="Zed"),
            raw(title="Abe 2% Milk, 1 gal", price="3.00", item="y", brand="Abe"),
        ],
    )
    first = min(
        Offer.objects.filter(retailer="fake"), key=lambda o: (o.product_id, o.pk)
    )
    for _ in range(3):
        assert resolve_item(item(), BOTH, NOW).best.offer.pk == first.pk


def test_mixed_currency_not_summed(milk, load, settings):
    load(milk, [raw(title="Acme 2% Milk, 1 gal", price="3.00", item="a", brand="Acme")])
    load(
        milk,
        [
            raw(
                title="Bravo 2% Milk, 1 gal",
                price="2.00",
                item="b",
                retailer="other",
                brand="Bravo",
            )
        ],
    )
    Offer.objects.filter(retailer="other").update(currency="CAD")
    # Offers outside the site currency are never fresh: CAD never reaches a total.
    out = resolve_list(lst(item()), BOTH, NOW)
    assert not out.mixed_currency and set(out.items[0].by_store) == {"fake"}
    assert out.split.total == Decimal("3.00")


def test_mixed_currency_withheld(milk, load, monkeypatch):
    load(
        milk,
        [
            raw(title="Acme 2% Milk, 1 gal", price="3.00", item="a", brand="Acme"),
            raw(
                title="Bravo 2% Milk, 1 gal",
                price="2.00",
                item="b",
                retailer="other",
                brand="Bravo",
            ),
        ],
    )
    Offer.objects.filter(retailer="other").update(currency="CAD")
    monkeypatch.setattr(
        "catalog.browse.fresh_offers", lambda now: Offer.objects.filter(kind="live")
    )
    out = resolve_list(lst(item()), BOTH, NOW)
    assert set(out.items[0].by_store) == {"fake", "other"}
    assert out.mixed_currency is True
    assert out.totals == [] and out.single is None and out.split is None
    assert out.currency is None and out.oldest_price_at is None


@pytest.mark.django_db
def test_generic_item_stays_in_dimension():
    make_generic_offer("Fizz Soda 2 L", "3.00", dim="volume", amount=2000, brand="Fizz")
    make_generic_offer(
        "Fizz Soda Powder 2 L", "0.50", dim="mass", amount=2000, brand="Fizz"
    )
    make_generic_offer(
        "Fizz Soda 2 L Unsized", "0.40", dim=None, amount=None, brand="Fizz"
    )
    res = resolve_item(gitem(), BOTH, NOW)
    assert res.reason is None and res.category is None
    assert res.best.offer.title == "Fizz Soda 2 L"
    assert {f.offer.measure_dim for f in res.by_store.values()} == {"volume"}
    # A brand facet matches the structured brand field, never a brand read from a title.
    make_generic_offer(
        "Fizz Soda 2 L by Pop", "1.00", dim="volume", amount=2000, brand=None
    )
    res = resolve_item(gitem(facets={"brand": ("fizz",)}), BOTH, NOW)
    assert res.best.offer.title == "Fizz Soda 2 L"


@pytest.mark.django_db
def test_generic_size_facet_uses_stored_unit_system(monkeypatch):
    make_generic_offer("Fizz Soda Big", "3.00", dim="volume", amount=1900)
    make_generic_offer("Fizz Soda Small", "1.00", dim="volume", amount=1000)
    seen = []
    orig = rs.generic_matching_offers

    def spy(*a, **kw):
        seen.append(kw.get("unit_system"))
        return orig(*a, **kw)

    monkeypatch.setattr(rs, "generic_matching_offers", spy)
    # vol_min 60 is fl oz in imperial (1774 mL) but 60 L in metric.
    res = resolve_item(
        gitem(facets={"vol_min": ("60",)}, unit_system="imperial"), BOTH, NOW
    )
    assert res.best.offer.title == "Fizz Soda Big"
    res = resolve_item(
        gitem(facets={"vol_min": ("60",)}, unit_system="metric"), BOTH, NOW
    )
    assert res.reason == "No current prices match at your stores"
    assert seen == ["imperial", "metric"]


@pytest.mark.django_db
def test_generic_never_uses_display_helpers(monkeypatch):
    make_generic_offer("Fizz Soda 2 L", "3.00", dim="volume", amount=2000)

    def boom(*a, **kw):
        raise AssertionError("generic items must not use curated target helpers")

    monkeypatch.setattr("catalog.units.default_target", boom)
    monkeypatch.setattr("catalog.units.display_target", boom)
    assert resolve_item(gitem(), BOTH, NOW).best is not None


@pytest.mark.django_db
def test_generic_unknown_facet_unresolved():
    make_generic_offer("Fizz Soda 2 L", "3.00", dim="volume", amount=2000)
    res = resolve_item(gitem(facets={"fat": ("2",), "brand": ("fizz",)}), BOTH, NOW)
    assert res.reason == "Filter no longer valid: fat" and res.best is None
    for bad in (
        {"brand_tier": ("gold",)},
        {"vol_min": ("lots",)},
        {"label": ("en:nope",)},
        {"path": ("a", "b")},
    ):
        res = resolve_item(gitem(facets=bad), BOTH, NOW)
        assert res.reason == f"Filter no longer valid: {next(iter(bad))}", bad


@pytest.mark.django_db
def test_generic_reasons_and_staleness():
    make_generic_offer("Fizz Soda Old", "0.10", dim="volume", amount=2000, age_hours=30)
    assert (
        resolve_item(gitem(), BOTH, NOW).reason
        == "No current prices match at your stores"
    )
    make_generic_offer("Fizz Soda Fresh", "2.00", dim="volume", amount=2000)
    res = resolve_item(gitem(), BOTH, NOW)
    assert res.best.offer.title == "Fizz Soda Fresh"
    assert (
        resolve_item(gitem(amount=Decimal(100000)), BOTH, NOW).reason
        == "Needs more than 24 packages"
    )


@pytest.mark.django_db
def test_resolve_list_is_pure(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("resolution must not enqueue")

    monkeypatch.setattr(rs, "enqueue", boom)
    resolve_list(lst(gitem(), item(4)), BOTH, NOW)
    assert FetchJob.objects.count() == 0


@pytest.mark.django_db
def test_queue_fetches_enqueues_list_jobs(monkeypatch):
    info = SourceInfo(name="src", budget_keys=(), refresh_after=timedelta(hours=6))
    monkeypatch.setattr(rs, "live_source_info", lambda **kw: {"src": info})
    calls = []

    def spy(phrase, origin, now, sources, *, client_key):
        calls.append((phrase, origin, client_key))
        return real_enqueue(phrase, origin, now, sources, client_key=client_key)

    monkeypatch.setattr(rs, "enqueue", spy)
    spec = lst(
        gitem(1, "fizz soda"),
        gitem(2, "Fizz  Soda", dim="mass"),
        gitem(3, "oat milk"),
        item(4),
    )
    rs.queue_fetches(spec, NOW, "key")
    assert sorted(calls) == [("fizz soda", "list", "key"), ("oat milk", "list", "key")]
    assert set(FetchJob.objects.values_list("phrase", "origin")) == {
        ("fizz soda", "list"),
        ("oat milk", "list"),
    }
    rs.queue_fetches(spec, NOW, "key")  # dedup: no new jobs
    assert FetchJob.objects.count() == 2


@pytest.mark.django_db
def test_queue_fetches_caps_phrases_and_skips_fresh(monkeypatch):
    info = SourceInfo(name="src", budget_keys=(), refresh_after=timedelta(hours=6))
    monkeypatch.setattr(rs, "live_source_info", lambda **kw: {"src": info})
    calls = []
    monkeypatch.setattr(rs, "enqueue", lambda phrase, *a, **kw: calls.append(phrase) or [])
    FetchJob.objects.create(
        source="src",
        phrase="aaa fresh",
        origin="search",
        status=FetchJob.Status.SUCCEEDED,
        finished_at=NOW - timedelta(hours=1),
        not_before=NOW - timedelta(hours=2),
    )
    spec = lst(
        gitem(1, "aaa fresh"),
        *[gitem(10 + n, f"phrase {n:02d}") for n in range(15)],
    )
    assert rs.queue_fetches(spec, NOW, None) == 10
    assert len(calls) == 10 and "aaa fresh" not in calls


@pytest.mark.django_db
def test_enqueue_failure_does_not_break_queueing(monkeypatch, caplog):
    monkeypatch.setattr(rs, "live_source_info", lambda **kw: {"src": SourceInfo(name="src", budget_keys=(), refresh_after=timedelta(hours=6))})

    def fail(*a, **kw):
        raise RuntimeError("secret phrase fizz soda")

    monkeypatch.setattr(rs, "enqueue", fail)
    rs.queue_fetches(lst(gitem()), NOW, None)
    assert "fizz" not in caplog.text and "RuntimeError" in caplog.text
