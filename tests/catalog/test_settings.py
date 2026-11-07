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
        "SHOPWIT_REFRESH_HOURS": "",
        "SHOPWIT_FRESHNESS_HOURS": "",
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
