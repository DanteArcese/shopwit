from datetime import timedelta

import pytest
from django.utils import timezone

from catalog.models import ReviewItem
from feedback.models import Feedback, StoreRequest
from feedback.text import store_key

pytestmark = pytest.mark.django_db

URL = "/admin/feedback/feedback/"


def make(kind="bug", message="hello", **kw):
    if kind == "store":
        kw.setdefault("store_name", "Trader Joe's")
        kw["store_key"] = store_key(kw["store_name"])
        message = kw.pop("message_override", "")
    return Feedback.objects.create(kind=kind, message=message, **kw)


def act(client, action, items, **extra):
    data = {"action": action, "_selected_action": [i.pk for i in items], **extra}
    return client.post(URL, data, follow=True)


def messages_text(response):
    return " ".join(str(m) for m in response.context["messages"])


def test_admin_pages_load(staff, offer):
    fb = make(kind="data", offer=offer)
    assert staff.get(URL).status_code == 200
    assert staff.get(f"{URL}{fb.pk}/change/").status_code == 200
    assert staff.get("/admin/feedback/storerequest/").status_code == 200


def test_approve_bug_creates_issue_with_label_and_safe_body(staff, token, gh):
    fb = make(
        "bug",
        "Broken for @octocat, see https://spam.example",
        reply_email="me@example.com",
    )
    act(staff, "approve", [fb])
    fb.refresh_from_db()
    assert fb.status == "synced" and fb.github_issue_number == 101
    [(method, path, payload, headers)] = gh.calls
    assert (method, path) == ("POST", "/repos/owner/repo/issues")
    assert headers["authorization"] == "Bearer test-token"
    assert payload["labels"] == ["bug"]
    assert "me@example.com" not in str(payload)
    assert "> Broken for `@octocat`, see `https://spam.example`" in payload["body"]
    assert "Submitted via unitpricer.com feedback" in payload["body"]
    assert "@octocat" not in payload["title"]


@pytest.mark.parametrize("kind,label", [("idea", "idea"), ("other", "feedback")])
def test_labels_per_kind(staff, token, gh, kind, label):
    act(staff, "approve", [make(kind)])
    assert gh.calls[0][2]["labels"] == [label]


def test_store_requests_dedup_into_one_issue(staff, token, gh):
    first = make("store", store_name="Trader Joe's")
    act(staff, "approve", [first])
    second = make("store", store_name="trader joes")
    third = make("store", store_name="The Trader Joes")
    act(staff, "approve", [second])
    act(staff, "approve", [third])

    creates = [c for c in gh.calls if c[0] == "POST"]
    updates = [c for c in gh.calls if c[0] == "PATCH"]
    assert len(creates) == 1
    assert creates[0][2]["title"] == "Store request: Trader Joe's"
    assert creates[0][2]["labels"] == ["store-request"]
    assert "Requested 1 time" in creates[0][2]["body"]
    assert [u[1] for u in updates] == ["/repos/owner/repo/issues/101"] * 2
    assert "Requested 2 times" in updates[0][2]["body"]
    assert "Requested 3 times" in updates[1][2]["body"]
    assert StoreRequest.objects.get().github_issue_number == 101
    assert set(Feedback.objects.values_list("status", "github_issue_number")) == {
        ("synced", 101)
    }


def test_store_issue_has_no_personal_data(staff, token, gh):
    fb = make(
        "store", store_name="Aldi", reply_email="me@example.com", website="aldi.us"
    )
    act(staff, "approve", [fb])
    payload = str(gh.calls[0][2])
    assert "me@example.com" not in payload


def test_unapproved_store_requests_not_counted(staff, token, gh):
    make("store", store_name="Aldi")  # never approved
    act(staff, "approve", [make("store", store_name="ALDI")])
    assert "Requested 1 time" in gh.calls[0][2]["body"]


def test_data_kind_creates_review_item_never_github(staff, token, gh, offer):
    a = make("data", "size is wrong", offer=offer)
    b = make("data", "price is wrong too", offer=offer)
    act(staff, "approve", [a])
    act(staff, "approve", [b])
    assert gh.calls == []
    item = ReviewItem.objects.get()
    assert (item.product, item.reason, item.status) == (
        offer.product,
        "user_report",
        "open",
    )
    a.refresh_from_db()
    b.refresh_from_db()
    assert a.review_item == b.review_item == item
    assert a.status == "approved" and a.github_issue_number is None


def test_data_kind_not_mergeable(staff, token, gh, offer):
    fb = make("data", "x", offer=offer)
    act(staff, "merge_into_issue", [fb], apply="1", issue_number="7")
    assert gh.calls == []
    fb.refresh_from_db()
    assert fb.status == "new"


def test_user_report_survives_ingest_merge(offer):
    from catalog.merge import merge_product

    from feedback.sync import link_review_item

    fb = make("data", "x", offer=offer)
    link_review_item(fb)
    merge_product(offer.product)
    assert ReviewItem.objects.get(reason="user_report").status == "open"


def test_token_unset_marks_approved_and_warns(staff, gh):
    fb = make("idea")
    r = act(staff, "approve", [fb])
    fb.refresh_from_db()
    assert fb.status == "approved" and fb.approved_at is not None
    assert "sync is disabled" in messages_text(r)
    assert gh.calls == []


def test_approved_items_sync_once_token_is_set(staff, gh, settings):
    fb = make("idea")
    act(staff, "approve", [fb])
    settings.GITHUB_TOKEN = "later"
    act(staff, "approve", [fb])
    fb.refresh_from_db()
    assert fb.status == "synced" and len(gh.calls) == 1
    act(staff, "approve", [fb])  # already synced: nothing more
    assert len(gh.calls) == 1


def test_github_error_keeps_item_approved(staff, token, monkeypatch):
    import httpx

    from feedback import github

    monkeypatch.setattr(
        github, "_transport", httpx.MockTransport(lambda r: httpx.Response(500))
    )
    fb = make("bug")
    r = act(staff, "approve", [fb])
    fb.refresh_from_db()
    assert fb.status == "approved" and fb.github_issue_number is None
    assert "failed" in messages_text(r)


def test_reject_clears_reply_email(staff, gh):
    fb = make("bug", reply_email="me@example.com")
    act(staff, "reject", [fb])
    fb.refresh_from_db()
    assert (fb.status, fb.reply_email) == ("rejected", "")
    assert gh.calls == []


def test_close_clears_reply_email(staff):
    fb = make("bug", reply_email="me@example.com", status="synced")
    act(staff, "close", [fb])
    fb.refresh_from_db()
    assert (fb.status, fb.reply_email) == ("closed", "")


def test_merge_asks_for_issue_number_then_comments(staff, token, gh):
    fb = make("bug", "same as before @someone")
    r = staff.post(URL, {"action": "merge_into_issue", "_selected_action": [fb.pk]})
    assert r.status_code == 200
    assert b'name="issue_number"' in r.content
    assert gh.calls == []

    act(staff, "merge_into_issue", [fb], apply="1", issue_number="42")
    [(method, path, payload, _)] = gh.calls
    assert (method, path) == ("POST", "/repos/owner/repo/issues/42/comments")
    assert payload["body"].startswith("+1")
    assert "`@someone`" in payload["body"]
    fb.refresh_from_db()
    assert (fb.status, fb.github_issue_number) == ("synced", 42)


def test_merge_rejects_bad_issue_number(staff, token, gh):
    fb = make("bug")
    r = act(staff, "merge_into_issue", [fb], apply="1", issue_number="abc")
    assert gh.calls == []
    assert b'name="issue_number"' in r.content


def test_merge_without_token_changes_nothing(staff, gh):
    fb = make("bug")
    r = act(staff, "merge_into_issue", [fb], apply="1", issue_number="42")
    fb.refresh_from_db()
    assert fb.status == "new"
    assert "sync is disabled" in messages_text(r)


def test_reply_email_cleanup(db):
    from feedback.cleanup import purge_reply_emails

    now = timezone.now()
    old = make("bug", reply_email="old@example.com")
    Feedback.objects.filter(pk=old.pk).update(created_at=now - timedelta(days=91))
    recent = make("bug", reply_email="new@example.com")
    closed = make("bug", reply_email="c@example.com")
    Feedback.objects.filter(pk=closed.pk).update(status="closed")  # bypasses save()

    assert purge_reply_emails(now) == 2
    emails = dict(Feedback.objects.values_list("pk", "reply_email"))
    assert emails == {old.pk: "", recent.pk: "new@example.com", closed.pk: ""}


def test_status_change_in_save_clears_email(db):
    fb = make("bug", reply_email="me@example.com")
    fb.status = "rejected"
    fb.save()
    fb.refresh_from_db()
    assert fb.reply_email == ""


# ---- review round 1 ----


@pytest.mark.parametrize("status", [401, 403, 422, 500, 502])
def test_http_error_matrix_records_status_only(staff, token, monkeypatch, caplog, status):
    import httpx

    from feedback import github

    secret = "SECRET-BODY-TEXT"
    monkeypatch.setattr(
        github, "_transport", httpx.MockTransport(lambda r: httpx.Response(status, text=secret))
    )
    a, b = make("bug"), make("idea")
    r = act(staff, "approve", [a, b])
    for fb in (a, b):  # a status error does not stop the batch
        fb.refresh_from_db()
        assert (fb.status, fb.sync_error, fb.github_issue_number) == ("approved", f"HTTP {status}", None)
    text = messages_text(r)
    assert f"HTTP {status}" in text
    assert secret not in text and secret not in caplog.text
    assert "test-token" not in text and "test-token" not in caplog.text
    assert f"HTTP {status}" in caplog.text
    assert f"HTTP {status}" in staff.get(URL).content.decode()


def test_sync_error_cleared_on_success(staff, token, gh):
    fb = make("bug", sync_error="HTTP 500")
    act(staff, "approve", [fb])
    fb.refresh_from_db()
    assert (fb.status, fb.sync_error) == ("synced", "")


def test_transport_error_aborts_batch(staff, token, monkeypatch):
    import httpx

    from feedback import github

    calls = []

    def boom(request):
        calls.append(request)
        raise httpx.ConnectTimeout("slow", request=request)

    monkeypatch.setattr(github, "_transport", httpx.MockTransport(boom))
    items = [make("bug"), make("idea"), make("other")]
    r = act(staff, "approve", items)
    assert len(calls) == 1
    text = messages_text(r)
    assert "2 item(s) not attempted" in text
    statuses = [Feedback.objects.get(pk=i.pk).status for i in items]
    assert statuses.count("new") == 2
    errors = [Feedback.objects.get(pk=i.pk).sync_error for i in items]
    assert sorted(errors) == ["", "", "ConnectTimeout"]


def test_client_timeout_is_short(settings):
    from feedback import github

    settings.GITHUB_TOKEN = "x"
    client = github.make_client()
    try:
        assert client._http.timeout.read == 8
    finally:
        client.close()


def test_store_title_neutralized(staff, token, gh):
    act(staff, "approve", [make("store", store_name="Bob's bob@example.com @octocat")])
    title = gh.calls[0][2]["title"]
    assert "example.com" not in title
    assert "`@octocat`" in title


def test_approved_count_excludes_closed_and_rejected(staff, token, gh):
    first = make("store", store_name="Aldi")
    act(staff, "approve", [first])
    act(staff, "close", [first])
    act(staff, "approve", [make("store", store_name="ALDI")])
    assert "Requested 1 time" in gh.calls[-1][2]["body"]
    assert StoreRequest.objects.get().approved_count() == 1


def test_store_issue_number_saved_before_later_db_error(staff, token, gh, monkeypatch):
    from feedback import sync

    def broken(*args, **kwargs):
        raise sync.DatabaseError("later failure")

    real = sync._mark_store_items_synced
    monkeypatch.setattr(sync, "_mark_store_items_synced", broken)
    fb = make("store", store_name="Aldi")
    with pytest.raises(sync.DatabaseError):
        sync.approve(fb, sync.github.make_client())
    assert StoreRequest.objects.get().github_issue_number == 101
    monkeypatch.setattr(sync, "_mark_store_items_synced", real)
    act(staff, "approve", [fb])
    assert [c[0] for c in gh.calls] == ["POST", "PATCH"]  # retry updates, never duplicates


def test_bug_issue_number_saved_before_later_db_error(token, gh, monkeypatch):
    from feedback import sync

    fb = make("bug")
    calls = []
    real_save = Feedback.save

    def save(self, *args, **kwargs):
        calls.append(kwargs.get("update_fields"))
        if kwargs.get("update_fields") and "status" in kwargs["update_fields"] and self.github_issue_number:
            raise sync.DatabaseError("later failure")
        return real_save(self, *args, **kwargs)

    monkeypatch.setattr(Feedback, "save", save)
    with pytest.raises(sync.DatabaseError):
        sync.approve(fb, sync.github.make_client())
    monkeypatch.setattr(Feedback, "save", real_save)
    fb.refresh_from_db()
    assert fb.github_issue_number == 101
    sync.approve(fb, sync.github.make_client())
    fb.refresh_from_db()
    assert fb.status == "synced" and len(gh.calls) == 1  # no duplicate issue


@pytest.mark.parametrize("status", ["rejected", "closed"])
def test_merge_excludes_rejected_and_closed(staff, token, gh, status):
    fb = make("bug", status=status)
    act(staff, "merge_into_issue", [fb], apply="1", issue_number="42")
    assert gh.calls == []
    fb.refresh_from_db()
    assert fb.status == status


def test_merge_survives_bad_response(staff, token, monkeypatch):
    import httpx

    from feedback import github

    monkeypatch.setattr(github, "_transport", httpx.MockTransport(lambda r: httpx.Response(422)))
    fb = make("bug")
    r = act(staff, "merge_into_issue", [fb], apply="1", issue_number="42")
    fb.refresh_from_db()
    assert (fb.status, fb.sync_error) == ("new", "HTTP 422")
    assert "HTTP 422" in messages_text(r)


def test_view_only_staff_cannot_act(client, db, gh, token):
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission

    user = get_user_model().objects.create_user("viewer", password="pw", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="view_feedback"))
    client.force_login(user)
    fb = make("bug")
    assert client.get(URL).status_code == 200
    act(client, "approve", [fb])
    fb.refresh_from_db()
    assert fb.status == "new" and gh.calls == []


def test_ingest_sync_resolves_ingest_items_but_keeps_user_report(offer):
    from django.utils import timezone as tz

    from catalog.merge import _sync_review_items

    product = offer.product
    ingest_item = ReviewItem.objects.create(product=product, detail="attribute:fat")
    user_item = ReviewItem.objects.create(
        product=product, detail="user report: wrong price or size", reason="user_report"
    )
    _sync_review_items(product, [], tz.now())
    ingest_item.refresh_from_db()
    user_item.refresh_from_db()
    assert ingest_item.status == "resolved"
    assert user_item.status == "open"
