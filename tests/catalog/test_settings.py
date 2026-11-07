from datetime import timedelta

import pytest
from django.core.management import call_command
from django.db import connection


@pytest.mark.django_db
def test_database_is_postgres():
    assert connection.vendor == "postgresql"


def test_refresh_and_freshness_defaults(settings):
    assert settings.REFRESH_INTERVAL == timedelta(hours=12)
    assert settings.FRESHNESS_CUTOFF == timedelta(hours=24)


def test_system_checks_pass():
    call_command("check")
