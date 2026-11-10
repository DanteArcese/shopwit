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


def test_dockerignore_excludes_secrets():
    from pathlib import Path

    lines = (Path(__file__).resolve().parents[2] / ".dockerignore").read_text().split()
    assert {".env", ".env.*", ".git", ".venv"} <= set(lines)


def test_blank_env_values_mean_unset():
    import os
    import subprocess
    import sys
    from pathlib import Path

    env = {
        **os.environ,
        "UNITPRICER_REFRESH_HOURS": "",
        "UNITPRICER_FRESHNESS_HOURS": "",
        "DJANGO_ALLOWED_HOSTS": "",
        "DJANGO_SECRET_KEY": "",
        "DJANGO_DEBUG": "1",
        "DATABASE_URL": "",
    }
    code = (
        "import config.settings as s;"
        "print(s.REFRESH_INTERVAL.total_seconds(), s.FRESHNESS_CUTOFF.total_seconds())"
    )
    root = Path(__file__).resolve().parents[2]
    out = subprocess.run(
        [sys.executable, "-c", code], env=env, cwd=root, capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["43200.0", "86400.0"]


@pytest.mark.parametrize(
    ("value", "default", "expected"),
    [
        (None, 5000, 5000),
        ("", 5000, 5000),
        ("  ", 5000, 5000),
        (None, None, None),
        ("250", 5000, 250),
        ("unlimited", 5000, None),
        ("UnLimited", 5000, None),
    ],
)
def test_budget_daily_limit(monkeypatch, value, default, expected):
    from config.settings import _budget

    monkeypatch.delenv("UNITPRICER_BUDGET_WALMART_DAILY_LIMIT", raising=False)
    if value is not None:
        monkeypatch.setenv("UNITPRICER_BUDGET_WALMART_DAILY_LIMIT", value)
    assert _budget("walmart", 5, 1.0, default)["daily_limit"] == expected


def test_budget_daily_limit_rejects_garbage(monkeypatch):
    from django.core.exceptions import ImproperlyConfigured

    from config.settings import _budget

    monkeypatch.setenv("UNITPRICER_BUDGET_WALMART_DAILY_LIMIT", "lots")
    with pytest.raises(ImproperlyConfigured):
        _budget("walmart", 5, 1.0, 5000)


@pytest.mark.parametrize("value", ["0", "-5"])
def test_budget_daily_limit_rejects_zero_and_negative(monkeypatch, value):
    from django.core.exceptions import ImproperlyConfigured

    from config.settings import _budget

    monkeypatch.setenv("UNITPRICER_BUDGET_WALMART_DAILY_LIMIT", value)
    with pytest.raises(ImproperlyConfigured):
        _budget("walmart", 5, 1.0, 5000)


@pytest.mark.parametrize("value", ["0.5", "0", "-1", "nan", "lots", "inf", "-inf", "1e999"])
def test_budget_capacity_must_be_at_least_one(monkeypatch, value):
    from django.core.exceptions import ImproperlyConfigured

    from config.settings import _budget

    monkeypatch.setenv("UNITPRICER_BUDGET_WALMART_CAPACITY", value)
    with pytest.raises(ImproperlyConfigured):
        _budget("walmart", 5, 1.0, 5000)


def test_openprices_budget_has_no_burst(monkeypatch):
    from config.settings import _budget

    monkeypatch.delenv("UNITPRICER_BUDGET_OPENPRICES_CAPACITY", raising=False)
    from django.conf import settings

    assert settings.SOURCE_BUDGETS["openprices"]["capacity"] == 1.0
    monkeypatch.setenv("UNITPRICER_BUDGET_WALMART_CAPACITY", "1")
    assert _budget("walmart", 5, 1.0, 5000)["capacity"] == 1.0


@pytest.mark.parametrize("value", ["-1", "nan", "inf", "1e999", "lots"])
def test_budget_refill_must_be_finite_and_not_negative(monkeypatch, value):
    from django.core.exceptions import ImproperlyConfigured

    from config.settings import _budget

    monkeypatch.setenv("UNITPRICER_BUDGET_WALMART_REFILL_PER_SECOND", value)
    with pytest.raises(ImproperlyConfigured):
        _budget("walmart", 5, 1.0, 5000)


@pytest.mark.parametrize(("value", "expected"), [("0", 0.0), ("0.25", 0.25), ("", 1.0)])
def test_budget_refill_accepts_zero_and_blank(monkeypatch, value, expected):
    from config.settings import _budget

    monkeypatch.setenv("UNITPRICER_BUDGET_WALMART_REFILL_PER_SECOND", value)
    assert _budget("walmart", 5, 1.0, 5000)["refill_per_second"] == expected


@pytest.mark.parametrize(("value", "expected"), [(None, 1), ("", 1), ("1", 1), ("2", 2), ("9", 2), ("0", 1)])
def test_fetch_threads(value, expected):
    from config.settings import _fetch_threads

    assert _fetch_threads(value) == expected


@pytest.mark.parametrize("value", ["two", "1.5", "nan"])
def test_fetch_threads_rejects_non_integers(value):
    from django.core.exceptions import ImproperlyConfigured

    from config.settings import _fetch_threads

    with pytest.raises(ImproperlyConfigured):
        _fetch_threads(value)


def _setting(name, value):
    """Read a setting in a fresh interpreter so the env var is parsed at import."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    env = {**os.environ, "DJANGO_SECRET_KEY": "x" * 50, "DJANGO_DEBUG": "1"}
    env.pop("UNITPRICER_OFF_PRODUCT_BOOST_UNTIL", None)
    env.pop("UNITPRICER_OFF_PRODUCT_BOOST_DAILY", None)
    env.update(value)
    out = subprocess.run(
        [sys.executable, "-c", f"import config.settings as s; print(repr(s.{name}))"],
        cwd=Path(__file__).resolve().parents[2], env=env, capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_boost_until_parses_iso_date():
    got = _setting("OFF_PRODUCT_BOOST_UNTIL", {"UNITPRICER_OFF_PRODUCT_BOOST_UNTIL": "2026-11-16"})
    assert got == "datetime.date(2026, 11, 16)"


@pytest.mark.parametrize("raw", [None, "", "  ", "garbage", "2026-13-40", "11/16/2026"])
def test_boost_until_unset_blank_or_malformed_means_no_boost(raw):
    value = {} if raw is None else {"UNITPRICER_OFF_PRODUCT_BOOST_UNTIL": raw}
    assert _setting("OFF_PRODUCT_BOOST_UNTIL", value) == "None"


@pytest.mark.parametrize("raw,expected", [(None, "2000"), ("5000", "5000"), ("0", "2000"), ("-3", "2000"), ("x", "2000")])
def test_boost_daily_defaults_and_ignores_bad_values(raw, expected):
    value = {} if raw is None else {"UNITPRICER_OFF_PRODUCT_BOOST_DAILY": raw}
    assert _setting("OFF_PRODUCT_BOOST_DAILY", value) == expected


def test_sovrn_links_use_the_commerce_api_key():
    # Sovrn: `key` is "Your Commerce API key, found in your Site Settings" (#102).
    import os
    import subprocess
    import sys
    from pathlib import Path

    env = {**os.environ, "DJANGO_DEBUG": "1", "SOVRN_API_KEY": "api-123"}
    code = "import config.settings as s; print(s.AFFILIATE['sovrn_api_key'])"
    root = Path(__file__).resolve().parents[2]
    out = subprocess.run(
        [sys.executable, "-c", code], env=env, cwd=root, capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "api-123"
