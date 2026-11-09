"""Move a visitor's session lists and settings into their account on sign-in.

Idempotent: lists and items are matched by their uid, so replaying a merge adds
nothing, and the account's own lists are never changed except to append items
that are not in them yet. Logs carry nothing (no ZIP, names or ids).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction

from lists.spec import MAX_ITEMS, MAX_LISTS
from lists.store import AccountListStore, SessionListStore
from web.prefs import DEFAULT_RADIUS, RADIUS_STEPS, clean_comparisons

from .models import UserSettings

LIMIT_MESSAGE = (
    "Some lists from this device didn't fit in your account "
    f"({MAX_LISTS} lists, {MAX_ITEMS} items each)."
)


@dataclass(frozen=True)
class MergeResult:
    lists_added: int
    items_added: int
    lists_skipped: int  # lists (or parts of lists) that hit a limit


def _merge_lists(session, user) -> MergeResult:
    store = AccountListStore(user)
    mine = {s.id: s for s in store.lists()}
    lists_added = items_added = lists_skipped = 0
    # Oldest first, so the limits keep the lists the visitor made first.
    for spec in reversed(SessionListStore(session).lists()):
        existing = mine.get(spec.id)
        if existing is None:
            if len(mine) >= MAX_LISTS:
                lists_skipped += 1
                continue
            kept = spec.items[:MAX_ITEMS]
            if len(kept) < len(spec.items):
                lists_skipped += 1
            store.save(replace(spec, items=kept))
            mine[spec.id] = replace(spec, items=kept)
            lists_added += 1
            items_added += len(kept)
            continue
        have = {i.id for i in existing.items}
        new = [i for i in spec.items if i.id not in have]
        room = MAX_ITEMS - len(existing.items)
        if len(new) > room:
            lists_skipped += 1
            new = new[: max(room, 0)]
        if not new:
            continue
        merged = replace(
            existing,
            items=(*existing.items, *new),
            updated_at=max(existing.updated_at, spec.updated_at),
        )
        store.save(merged)
        mine[spec.id] = merged
        items_added += len(new)
    return MergeResult(lists_added, items_added, lists_skipped)


def _session_settings(raw) -> dict | None:
    """Validated settings from the session's prefs, or None when there are none."""
    if not isinstance(raw, dict):
        return None
    unit_system = raw.get("unit_system")
    stores = raw.get("stores")
    radius = raw.get("radius", DEFAULT_RADIUS)
    postal = raw.get("postal_code")
    country = raw.get("country", "US")
    return {
        "unit_system": unit_system if unit_system in ("imperial", "metric") else "",
        "stores": stores
        if isinstance(stores, list) and stores and all(isinstance(s, str) for s in stores)
        else None,
        "postal_code": postal if isinstance(postal, str) and len(postal) <= 10 else "",
        "country": country if isinstance(country, str) and len(country) == 2 else "US",
        "radius": radius
        if not isinstance(radius, bool) and radius in RADIUS_STEPS
        else DEFAULT_RADIUS,
        "comparisons": clean_comparisons(raw.get("comparisons")),
    }


def _merge_prefs(session, user) -> None:
    incoming = _session_settings(session.get("prefs"))
    if incoming is None:
        return
    row = UserSettings.objects.filter(user=user).first()
    if row is None:
        UserSettings.objects.create(user=user, **incoming)
        return
    if row.unit_system == "":
        # The account never chose settings: the device's selection fills them.
        row.unit_system = incoming["unit_system"]
        row.stores = incoming["stores"]
    if not row.postal_code and incoming["postal_code"]:
        row.postal_code = incoming["postal_code"]
        row.country = incoming["country"]
        if row.radius == DEFAULT_RADIUS:
            row.radius = incoming["radius"]
    row.comparisons = {**incoming["comparisons"], **clean_comparisons(row.comparisons)}
    row.save()


def merge_session_into_account(session, user) -> MergeResult:
    with transaction.atomic():
        # Serialize concurrent sign-ins of one account (two devices at once).
        get_user_model().objects.select_for_update().get(pk=user.pk)
        result = _merge_lists(session, user)
        _merge_prefs(session, user)
    session.pop("lists", None)
    session.pop("prefs", None)
    return result


def on_user_logged_in(sender, request, user, **kwargs):
    if request is None or not hasattr(request, "session"):
        return
    result = merge_session_into_account(request.session, user)
    if result.lists_skipped:
        messages.warning(request, LIMIT_MESSAGE, fail_silently=True)
