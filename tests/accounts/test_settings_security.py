import os
import subprocess
import sys
from pathlib import Path

from django.core.checks import run_checks

from accounts.checks import check_site_url

ROOT = Path(__file__).resolve().parents[2]


def _settings(code, **env):
    full = {**os.environ, "DATABASE_URL": "", "DJANGO_SECRET_KEY": "x" * 50, **env}
    out = subprocess.run(
        [sys.executable, "-c", "import config.settings as s;" + code],
        env=full, cwd=ROOT, capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.split()


def test_secure_cookies_when_not_debug():
    assert _settings(
        "print(s.SESSION_COOKIE_SECURE, s.CSRF_COOKIE_SECURE, s.SESSION_COOKIE_HTTPONLY, 'accounts' in s.INSTALLED_APPS)",
        DJANGO_DEBUG="0",
    ) == ["True", "True", "True", "True"]


def test_console_email_backend_in_debug():
    assert _settings("print(s.EMAIL_BACKEND)", DJANGO_DEBUG="1", DJANGO_EMAIL_BACKEND="") == [
        "django.core.mail.backends.console.EmailBackend"
    ]


def test_magic_link_ttl_and_login_url(settings):
    from datetime import timedelta

    assert settings.MAGIC_LINK_TTL == timedelta(minutes=15)
    assert settings.LOGIN_URL == "accounts:sign_in"
    assert len(settings.AUTH_PASSWORD_VALIDATORS) >= 4


def test_site_url_check(settings):
    settings.DEBUG = False
    for bad in ("", "http://beta.unitpricer.com"):
        settings.SITE_URL = bad
        assert [e.id for e in check_site_url(None)] == ["unitpricer.E010"]
    settings.SITE_URL = "https://beta.unitpricer.com"
    assert check_site_url(None) == []
    settings.DEBUG = True
    settings.SITE_URL = ""
    assert check_site_url(None) == []


def test_site_url_check_registered_for_deploy(settings):
    settings.DEBUG = False
    settings.SITE_URL = ""
    ids = [e.id for e in run_checks(include_deployment_checks=True)]
    assert "unitpricer.E010" in ids
