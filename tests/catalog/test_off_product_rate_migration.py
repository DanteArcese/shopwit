import importlib

import pytest
from django.apps import apps

from catalog.models import SourceBudget

mig = importlib.import_module("catalog.migrations.0012_off_product_rate")


def row(refill):
    SourceBudget.objects.all().delete()
    return SourceBudget.objects.create(
        source="off-product",
        capacity=1.0,
        refill_per_second=refill,
        tokens=1.0,
        refilled_at="2026-11-09T00:00:00Z",
        daily_limit=300,
        daily_used=0,
        day="2026-11-09",
    )


def value():
    return SourceBudget.objects.get(pk="off-product").refill_per_second


@pytest.mark.django_db
def test_old_default_is_slowed_and_reversed():
    row(1.0)
    mig.slow(apps, None)
    assert value() == 0.5
    mig.restore(apps, None)
    assert value() == 1.0


@pytest.mark.django_db
def test_admin_edits_are_left_alone():
    row(0.8)
    mig.slow(apps, None)
    assert value() == 0.8
    mig.restore(apps, None)
    assert value() == 0.8


@pytest.mark.django_db
def test_other_sources_untouched():
    row(1.0)
    SourceBudget.objects.create(
        source="walmart", capacity=5.0, refill_per_second=1.0, tokens=5.0,
        refilled_at="2026-11-09T00:00:00Z", daily_limit=5000, daily_used=0, day="2026-11-09",
    )
    mig.slow(apps, None)
    assert SourceBudget.objects.get(pk="walmart").refill_per_second == 1.0
