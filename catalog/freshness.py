"""Offer freshness: an offer is hidden once older than min(FRESHNESS_CUTOFF, connector max cache age)."""

from __future__ import annotations

from datetime import datetime, timedelta
from importlib import import_module

from django.conf import settings
from django.db.models import Q, QuerySet

from connectors import registry

from .models import Offer


def max_cache_age(connector_name: str) -> timedelta | None:
    """The registered connector class's max_cache_age, without constructing it."""
    spec = registry.SPECS.get(connector_name)
    if spec is None:
        return None
    module_name, _, cls_name = spec.path.partition(":")
    return getattr(getattr(import_module(module_name), cls_name), "max_cache_age", None)


def cutoff_for(connector_name: str, now: datetime) -> datetime:
    limit = settings.FRESHNESS_CUTOFF
    age = max_cache_age(connector_name)
    if age is not None:
        limit = min(limit, age)
    return now - limit


def fresh_offers(now: datetime) -> QuerySet[Offer]:
    registered = list(registry.SPECS)
    query = Q(pk__in=[])  # matches nothing; OR-ed with the branches below
    for name in registered:
        query |= Q(retailer=name, last_seen_at__gte=cutoff_for(name, now))
    # Retailers without a registered connector fall back to the setting.
    query |= ~Q(retailer__in=registered) & Q(last_seen_at__gte=now - settings.FRESHNESS_CUTOFF)
    return Offer.objects.filter(query, kind=Offer.Kind.LIVE, currency=settings.SITE_CURRENCY)


def fresh_observed(now: datetime) -> QuerySet[Offer]:
    """Observed (Open Prices) offers, aged by observation date rather than sync time."""
    oldest = (now - settings.OBSERVED_MAX_AGE).date()
    return Offer.objects.filter(
        kind=Offer.Kind.OBSERVED, observed_on__gte=oldest, currency=settings.SITE_CURRENCY
    )
