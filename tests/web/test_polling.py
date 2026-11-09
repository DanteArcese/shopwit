import re
from datetime import timedelta
from pathlib import Path
from html import unescape
from urllib.parse import parse_qs, urlparse

import pytest
from django.core.cache import cache

from catalog.models import FetchJob, SearchDemand, SourceBudget
from connectors.live import SourceInfo
from tests.catalog.helpers import NOW
from tests.catalog.test_generic_match import make_generic_offer
from web.search import POLL_WINDOW, JobState, live_status, should_poll

pytestmark = pytest.mark.usefixtures("frozen_now")

WALMART = SourceInfo("walmart", ("walmart",), timedelta(hours=12))
START = int(NOW.timestamp())


@pytest.fixture(autouse=True)
def sources(monkeypatch, db):
    cache.clear()
    found = {"walmart": WALMART}
    monkeypatch.setattr("web.search.live_source_info", lambda **kw: found)
    SourceBudget.objects.create(
        source="walmart", capacity=5.0, refill_per_second=1.0, tokens=5.0,
        refilled_at=NOW, daily_limit=None, daily_used=0, day=NOW.date(),
    )
    return found


def state(status, finished=None, count=0, source="walmart"):
    return JobState(source, status, finished, count)


def page(client, q="oat milk", **extra):
    resp = client.get("/search", {"q": q}, **extra)
    assert resp.status_code == 200
    return resp, resp.content.decode()


def poll(client, q="oat milk", start=START, seen=START):
    resp = client.get(
        "/search", {"q": q, "poll": start, "seen": seen}, HTTP_HX_REQUEST="true"
    )
    assert resp.status_code == 200
    return resp, resp.content.decode()


def finish(status="succeeded", count=0, offset=1):
    return FetchJob.objects.filter(source="walmart").update(
        status=status, result_count=count, finished_at=NOW + timedelta(seconds=offset)
    )


def test_polling_stop_condition():
    queued = [state("queued")]
    assert should_poll(queued, START, START + 3)
    assert not should_poll([state("succeeded", NOW), state("failed", NOW)], START, START + 3)
    assert not should_poll(queued, START, START + POLL_WINDOW)
    assert should_poll(queued, START, START + POLL_WINDOW - 1)
    assert not should_poll(queued, START + 5, START)


def test_stop_renders_poller_without_trigger(client):
    page(client)
    finish()
    _, body = poll(client)
    assert 'id="poller"' in body and "every 2s" not in body


def test_first_render_enqueues_and_polls(client):
    _, body = page(client)
    job = FetchJob.objects.get()
    assert (job.origin, job.source, job.phrase) == ("search", "walmart", "oat milk")
    assert 'hx-trigger="every 2s"' in body and 'hx-swap="outerHTML"' in body
    assert "checking Walmart…" in body
    assert 'role="status"' in body


def test_idle_poll_is_cheap(client, django_assert_max_num_queries):
    page(client)
    with django_assert_max_num_queries(2):
        _, body = poll(client)
    assert 'hx-trigger="every 2s"' in body
    assert 'id="results"' not in body
    assert FetchJob.objects.count() == 1
    assert SearchDemand.objects.get(phrase="oat milk").count == 1  # the page counted; the poll did not


def test_poll_after_job_finishes_swaps_results(client):
    page(client)
    for n in range(3):
        make_generic_offer(f"Oat Milk Brand{n}", "2", dim="volume", amount=1000)
    finish(count=3)
    _, body = poll(client)
    assert 'hx-swap-oob="innerHTML"' in body and "Oat Milk Brand0" in body
    assert "updated with 3 new products" in body
    assert 'id="results-status"' in body
    assert "hx-trigger" not in body  # all jobs are terminal: stop


def test_poll_keeps_filters_and_status(client):
    page(client)
    make_generic_offer("Oat Milk Acme", "2", dim="volume", amount=1000, brand="Acme")
    make_generic_offer("Oat Milk Bravo", "2", dim="volume", amount=1000, brand="Bravo")
    FetchJob.objects.update(origin="search")
    resp = client.get(
        "/search", {"q": "oat milk", "poll": START, "seen": START, "brand": "Acme"},
        HTTP_HX_REQUEST="true",
    )
    finish_body = resp.content.decode()
    # idle: still queued, nothing finished, so only the poller; no results to filter
    assert 'id="results"' not in finish_body
    finish()
    resp = client.get(
        "/search", {"q": "oat milk", "poll": START, "seen": START, "brand": "Acme"},
        HTTP_HX_REQUEST="true",
    )
    body = resp.content.decode()
    assert "Oat Milk Acme" in body and "Oat Milk Bravo" not in body
    assert 'id="facets" hx-swap-oob="true"' in body
    assert "1 product" in body


def test_timeout_message(client):
    page(client)
    _, body = poll(client, start=START - 16, seen=START - 16)
    assert "every 2s" not in body
    assert "Still checking Walmart. Reload in a minute for more." in body


def test_failed_source_message(client):
    page(client)
    finish("failed")
    _, body = poll(client)
    assert "Walmart didn't respond; showing saved prices." in unescape(body)


def test_busy_when_refused(client):
    SourceBudget.objects.filter(source="walmart").update(blocked_until=NOW + timedelta(hours=1))
    _, body = page(client)
    assert FetchJob.objects.count() == 0
    assert "every 2s" not in body
    assert "Live search is busy; showing saved prices." in body


def test_recent_success_is_not_busy(client):
    FetchJob.objects.create(
        source="walmart", phrase="oat milk", status="succeeded", not_before=NOW,
        finished_at=NOW - timedelta(hours=1),
    )
    _, body = page(client)
    assert "busy" not in body and "every 2s" not in body


def test_nothing_found_after_polling(client):
    _, body = page(client, "zzz")
    assert "No saved prices match &lsquo;zzz&rsquo;" in body and "checking Walmart…" in body
    finish(count=0)
    _, body = poll(client, "zzz")
    assert "Nothing found. Try fewer words or" in body
    assert "No saved prices match" not in body


def test_personal_phrase_no_job_no_poller(client):
    make_generic_offer("Milk 555-123-4567 Special", "2", dim="volume", amount=1000)
    _, body = page(client, "555-123-4567 milk")
    assert FetchJob.objects.count() == 0
    assert SearchDemand.objects.count() == 0
    assert '<div id="poller"></div>' in body  # the empty placeholder: no hx-get, no trigger
    assert "every 2s" not in body and "poll=" not in body
    assert "checking" not in body and "busy" not in body


def test_poll_sets_no_cookies(client):
    first, _ = page(client)
    assert not first.cookies
    second, _ = poll(client)
    assert not second.cookies
    finish()
    third, _ = poll(client)
    assert not third.cookies


@pytest.mark.parametrize("q", ['"><script>x</script>', "milk&poll=1", "milk%26store%3Dx"])
def test_poller_url_escapes_query(client, q):
    _, body = page(client, q)
    attr = re.search(r'id="poller" hx-get="([^"]*)"', body)
    assert attr, body
    params = parse_qs(urlparse(unescape(attr.group(1))).query, keep_blank_values=True)
    assert len(params["poll"]) == 1 and len(params["q"]) == 1 and len(params["seen"]) == 1
    assert params["q"] == [q]
    assert set(params) == {"q", "poll", "seen"}
    assert "<script>x" not in body


def test_htmx_facet_change_does_not_enqueue(client):
    resp = client.get("/search", {"q": "oat milk", "brand": "Acme"}, HTTP_HX_REQUEST="true")
    assert resp.status_code == 200
    assert FetchJob.objects.count() == 0


@pytest.mark.parametrize(
    "extra",
    [{"HTTP_USER_AGENT": "Mozilla/5.0 (compatible; Googlebot/2.1)"}, {"HTTP_USER_AGENT": "curl/8.0"}],
)
def test_bots_and_head_do_not_enqueue(client, extra):
    page(client, **extra)
    assert FetchJob.objects.count() == 0
    resp = client.head("/search", {"q": "oat milk"})
    assert resp.status_code == 200
    assert FetchJob.objects.count() == 0


def test_invalid_poll_values_stop(client):
    page(client)
    for params in ({"poll": "x", "seen": "1"}, {"poll": str(START + 99), "seen": str(START)}):
        resp = client.get("/search", {"q": "oat milk", **params}, HTTP_HX_REQUEST="true")
        assert resp.status_code == 200 and "hx-trigger" not in resp.content.decode()


@pytest.mark.parametrize("value", ["99999999999999", "-5", "1e30", str(START - 3601), str(START + 61)])
@pytest.mark.parametrize("which", ["poll", "seen"])
def test_out_of_range_poll_values_stop(client, value, which):
    page(client)
    params = {"poll": str(START), "seen": str(START), which: value}
    resp = client.get("/search", {"q": "oat milk", **params}, HTTP_HX_REQUEST="true")
    assert resp.status_code == 200
    assert "hx-trigger" not in resp.content.decode()


def test_history_restore_does_not_enqueue(client):
    resp = client.get(
        "/search", {"q": "oat milk"}, HTTP_HX_REQUEST="true", HTTP_HX_HISTORY_RESTORE_REQUEST="true"
    )
    assert resp.status_code == 200
    assert "<html" in resp.content.decode()  # the full page, as a cache miss needs
    assert FetchJob.objects.count() == 0
    assert SearchDemand.objects.count() == 0


OFF = SourceInfo("off-prices", ("off-search", "openprices"), timedelta(hours=24))


def test_busy_persists_across_polls(client, sources):
    sources["off-prices"] = OFF
    SourceBudget.objects.create(
        source="off-search", capacity=5.0, refill_per_second=1.0, tokens=5.0,
        refilled_at=NOW, daily_limit=None, daily_used=0, day=NOW.date(),
        blocked_until=NOW + timedelta(hours=1),
    )
    _, body = page(client)
    assert list(FetchJob.objects.values_list("source", flat=True)) == ["walmart"]
    assert "Live search is busy; showing saved prices." in body
    make_generic_offer("Oat Milk Acme", "2", dim="volume", amount=1000)
    finish(count=1)
    _, body = poll(client)
    assert 'id="results"' in body
    assert "Live search is busy; showing saved prices." in body
    assert "updated with 1 new product" in body and "1 new products" not in body


def test_status_notes_are_visible(client):
    page(client)
    finish("failed")
    _, body = poll(client)
    results = body[body.index('id="results"'):]
    assert 'class="search-notes"' in results
    assert "Walmart didn&#x27;t respond; showing saved prices." in results
    assert 'role="status"' not in results  # the live region stays where it was
    _, body = poll(client, start=START - 16, seen=START - 16)
    assert 'class="search-notes"' in body


def test_still_checking_note_is_visible(client):
    _, body = page(client)
    notes = body[body.index('class="search-notes"'):]
    assert "checking Walmart…" in notes[: notes.index("</")]
    assert 'role="status"' in body and 'aria-live="polite"' in body


def _settings_next(body):
    link = re.search(r'id="settings-link"[^>]*href="([^"]*)"', body)
    assert link, body
    return parse_qs(urlparse(unescape(link.group(1))).query)["next"][0]


def test_settings_link_from_query_and_facets_only(client):
    page(client)
    make_generic_offer("Oat Milk Acme", "2", dim="volume", amount=1000, brand="Acme")
    finish(count=1)
    resp = client.get(
        "/search",
        {"q": "oat milk", "poll": START, "seen": START, "brand": "acme", "junk": "1", "sec": "observed"},
        HTTP_HX_REQUEST="true",
    )
    nxt = _settings_next(resp.content.decode())
    params = parse_qs(urlparse(nxt).query)
    assert urlparse(nxt).path == "/search"
    assert params == {"q": ["oat milk"], "brand": ["acme"]}
    _, body = page(client)
    assert parse_qs(urlparse(_settings_next(body)).query) == {"q": ["oat milk"]}


def test_poll_does_not_reset_facets_drawer_or_inputs(client):
    page(client)
    for n, brand in enumerate(["Acme", "Bravo"]):
        make_generic_offer(f"Oat Milk {brand}", str(2 + n), dim="volume", amount=1000 + n, brand=brand)
    finish(count=2)
    _, body = poll(client)
    facets = re.search(r'<div id="facets"([^>]*)>', body)
    assert facets and 'hx-swap-oob="true"' in facets.group(1)
    assert "data-poll" in facets.group(1)  # app.js keeps drawer state and a focused input
    script = (Path(__file__).resolve().parents[2] / "web/static/web/app.js").read_text()
    assert "htmx:oobBeforeSwap" in script and "data-poll" in script and "shouldSwap" in script


def test_live_status_messages():
    q, s = state("queued"), state("succeeded", NOW, 2)
    assert live_status([q], True, False) == "checking Walmart…"
    assert live_status([q], False, False) == "Still checking Walmart. Reload in a minute for more."
    assert live_status([s], False, False) == "updated with 2 new products"
    assert live_status([state("succeeded", NOW, 1)], False, False) == "updated with 1 new product"
    assert live_status([], False, True) == "Live search is busy; showing saved prices."
    assert live_status([], False, False) is None
    both = live_status([state("queued"), state("queued", source="off-prices")], True, False)
    assert both == "checking Open Food Facts and Walmart…"


def test_disclosure_sentence(client, settings):
    settings.LIVE_OFF_ENABLED = True
    body = client.get("/disclosure").content.decode()
    assert "When you search for something we haven&#x27;t seen" in body or "haven't seen" in body
    assert "We send only the search words." in body


def test_poll_with_rank_renders_ranked_groups(client):
    from tests.catalog.test_generic_match import nutrify_offer

    page(client)
    nutrify_offer(make_generic_offer("Oat Milk Acme", "4", dim="mass", amount=500), protein=20.0)
    make_generic_offer("Oat Milk Bare", "1", dim="mass", amount=500)
    finish(count=2)
    resp = client.get(
        "/search", {"q": "oat milk", "poll": START, "seen": START, "rank": "protein"},
        HTTP_HX_REQUEST="true",
    )
    body = resp.content.decode()
    assert "$4.00 per 100 g protein" in body and "Can't rank by protein per $" in body
    assert body.index("Oat Milk Acme") < body.index("Oat Milk Bare")
    facets = body[body.index('id="facets"'):]
    assert '<option value="protein" selected>Protein per $</option>' in facets
    assert "Nutrition: <a" in body
    assert parse_qs(urlparse(_settings_next(body)).query) == {"q": ["oat milk"], "rank": ["protein"]}
