import logging
import sys

import pytest
from django.core.management import call_command

SMTP = "django.core.mail.backends.smtp.EmailBackend"


def _print_settings(run_prod, env, *names):
    code = "import config.settings as s;" + ";".join(f"print('{n}', repr(s.{n}))" for n in names)
    out = run_prod(env, "-c", code)
    assert out.returncode == 0, out.stderr
    return dict(line.split(" ", 1) for line in out.stdout.splitlines())


def test_check_deploy_clean(prod_env, run_prod):
    out = run_prod(prod_env, "manage.py", "check", "--deploy", "--fail-level", "WARNING")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "System check identified no issues" in out.stdout + out.stderr


def test_check_deploy_flags_missing_hosts(prod_env, run_prod):
    del prod_env["UNITPRICER_PUBLIC_HOST"]
    out = run_prod(prod_env, "manage.py", "check", "--deploy", "--fail-level", "WARNING")
    assert out.returncode != 0
    assert "unitpricer.E020" in out.stdout + out.stderr


def test_check_deploy_flags_bad_public_url(prod_env, run_prod):
    prod_env["UNITPRICER_PUBLIC_URL"] = "http://unitpricer.com"
    out = run_prod(prod_env, "manage.py", "check", "--deploy", "--fail-level", "WARNING")
    assert out.returncode != 0
    assert "unitpricer.E021" in out.stdout + out.stderr


def test_prod_cookie_and_proxy_settings(prod_env, run_prod):
    got = _print_settings(
        run_prod,
        prod_env,
        "SESSION_COOKIE_SECURE",
        "CSRF_COOKIE_SECURE",
        "SECURE_PROXY_SSL_HEADER",
        "SECURE_HSTS_SECONDS",
        "SECURE_SSL_REDIRECT",
        "CSRF_TRUSTED_ORIGINS",
    )
    assert got["SESSION_COOKIE_SECURE"] == "True"
    assert got["CSRF_COOKIE_SECURE"] == "True"
    assert got["SECURE_PROXY_SSL_HEADER"] == "('HTTP_X_FORWARDED_PROTO', 'https')"
    assert got["SECURE_HSTS_SECONDS"] == "3600"
    assert got["SECURE_SSL_REDIRECT"] == "True"
    assert "https://beta.unitpricer.com" in got["CSRF_TRUSTED_ORIGINS"]


def test_secret_key_fallbacks_from_env(prod_env, run_prod):
    prod_env["DJANGO_SECRET_KEY_FALLBACKS"] = "old-one, old-two"
    got = _print_settings(run_prod, prod_env, "SECRET_KEY_FALLBACKS")
    assert got["SECRET_KEY_FALLBACKS"] == repr(["old-one", "old-two"])


def test_smtp_settings_from_env(prod_env, run_prod):
    prod_env.update(
        DJANGO_EMAIL_HOST="smtp.resend.com",
        DJANGO_EMAIL_PORT="2587",
        DJANGO_EMAIL_HOST_USER="resend",
        RESEND_API_KEY="re_test",
    )
    got = _print_settings(
        run_prod,
        prod_env,
        "EMAIL_BACKEND",
        "EMAIL_HOST",
        "EMAIL_PORT",
        "EMAIL_HOST_USER",
        "EMAIL_HOST_PASSWORD",
        "EMAIL_USE_TLS",
        "DEFAULT_FROM_EMAIL",
    )
    assert got["EMAIL_BACKEND"] == repr(SMTP)
    assert got["EMAIL_HOST"] == "'smtp.resend.com'"
    assert got["EMAIL_PORT"] == "2587"
    assert got["EMAIL_HOST_USER"] == "'resend'"
    assert got["EMAIL_HOST_PASSWORD"] == "'re_test'"
    assert got["EMAIL_USE_TLS"] == "True"
    assert got["DEFAULT_FROM_EMAIL"] == "'UnitPricer <hello@unitpricer.com>'"


def test_console_email_backend_in_debug(prod_env, run_prod):
    # pytest-django forces DEBUG off and swaps the mail backend, so read the real setting.
    prod_env["DJANGO_DEBUG"] = "1"
    got = _print_settings(run_prod, prod_env, "EMAIL_BACKEND", "SECURE_SSL_REDIRECT", "SECURE_HSTS_SECONDS")
    assert got["EMAIL_BACKEND"] == repr("django.core.mail.backends.console.EmailBackend")
    assert got["SECURE_SSL_REDIRECT"] == "False"
    assert got["SECURE_HSTS_SECONDS"] == "0"


def test_logging_goes_to_stdout(prod_env, run_prod):
    code = (
        "import os, django, logging;"
        "os.environ['DJANGO_SETTINGS_MODULE']='config.settings';"
        "django.setup();"
        "logging.getLogger('catalog.scheduler').info('hi')"
    )
    out = run_prod(prod_env, "-c", code)
    assert out.returncode == 0, out.stderr
    assert out.stdout.count("INFO catalog.scheduler hi") == 1
    assert "hi" not in out.stderr


def _spy_run_loop(monkeypatch):
    """Log one marker line from inside the command; record the logger's own handler count."""
    from catalog import scheduler

    seen = []
    orig = scheduler.run_loop

    def spy(*a, **kw):
        scheduler.logger.warning("marker")
        seen.append(len(scheduler.logger.handlers))
        return orig(*a, **kw)

    monkeypatch.setattr(scheduler, "run_loop", spy)
    return seen


@pytest.mark.django_db
def test_run_scheduler_logs_once_with_root_handler(capsys, monkeypatch):
    # Production shape: root already has a stdout handler, so the command adds none.
    monkeypatch.setattr(logging.getLogger(), "handlers", [logging.StreamHandler(sys.stdout)])
    seen = _spy_run_loop(monkeypatch)
    call_command("run_scheduler", "--once")
    captured = capsys.readouterr()
    assert (captured.out + captured.err).count("marker") == 1
    assert seen == [0]


@pytest.mark.django_db
def test_run_scheduler_attaches_handler_when_none(capsys, monkeypatch):
    monkeypatch.setattr(logging.getLogger(), "handlers", [])
    seen = _spy_run_loop(monkeypatch)
    call_command("run_scheduler", "--once")
    captured = capsys.readouterr()
    assert (captured.out + captured.err).count("marker") == 1
    assert seen == [1]


def test_check_deploy_flags_missing_access_aud(prod_env, run_prod):
    del prod_env["CF_ACCESS_AUD"]
    out = run_prod(prod_env, "manage.py", "check", "--deploy", "--fail-level", "WARNING")
    assert out.returncode != 0
    assert "unitpricer.E022" in out.stdout + out.stderr


def test_check_deploy_flags_beta_site_url(prod_env, run_prod):
    prod_env["UNITPRICER_SITE_URL"] = "https://beta.unitpricer.com"
    out = run_prod(prod_env, "manage.py", "check", "--deploy", "--fail-level", "WARNING")
    assert out.returncode != 0
    assert "unitpricer.E023" in out.stdout + out.stderr


def test_prod_cookies_host_only(prod_env, run_prod):
    # A Domain attribute would share apex sessions/CSRF tokens with beta (and back).
    code = (
        "import os, django;"
        "os.environ['DJANGO_SETTINGS_MODULE']='config.settings';"
        "django.setup();"
        "from django.conf import settings as s;"
        "print(repr(s.SESSION_COOKIE_DOMAIN), repr(s.CSRF_COOKIE_DOMAIN), repr(s.SITE_URL), repr(s.PUBLIC_URL))"
    )
    out = run_prod(prod_env, "-c", code)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["None", "None", "'https://unitpricer.com'", "'https://unitpricer.com'"]
