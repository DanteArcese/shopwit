from web.glogging import RedactingLogger, redact


def test_redacts_waitlist_tokens():
    assert redact("/waitlist/confirm/abc:def-ghi") == "/waitlist/confirm/…"
    assert redact("GET /waitlist/unsubscribe/tok123 HTTP/1.1") == "GET /waitlist/unsubscribe/… HTTP/1.1"
    assert redact("/waitlist/") == "/waitlist/"
    assert redact("/privacy") == "/privacy"


def test_logger_atoms_redacted(monkeypatch):
    from gunicorn.glogging import Logger

    monkeypatch.setattr(Logger, "atoms", lambda *a: {"U": "/waitlist/confirm/SECRET", "m": "GET"})
    atoms = RedactingLogger.__new__(RedactingLogger).atoms(None, None, None, 0)
    assert atoms == {"U": "/waitlist/confirm/…", "m": "GET"}


def test_gunicorn_conf():
    import runpy

    conf = runpy.run_path("deploy/gunicorn.conf.py")
    assert conf["control_socket_disable"] is True
    assert conf["logger_class"] == "web.glogging.RedactingLogger"


def test_redacts_magic_link_tokens():
    assert redact("/account/magic/AbC_d-9") == "/account/magic/…"
    assert redact("POST /account/magic/tok HTTP/1.1") == "POST /account/magic/… HTTP/1.1"
    assert redact("/account/magic-link") == "/account/magic-link"


def test_log_filter_redacts_django_records():
    import logging

    from web.glogging import RedactFilter

    record = logging.LogRecord("django.request", logging.ERROR, __file__, 1, "Internal Server Error: %s", ("/account/magic/SECRET",), None)
    assert RedactFilter().filter(record) is True
    assert record.getMessage() == "Internal Server Error: /account/magic/…"


def test_settings_logging_uses_redact_filter():
    from django.conf import settings

    assert "redact" in settings.LOGGING["handlers"]["stdout"]["filters"]


def test_log_filter_never_raises_on_bad_records():
    import logging

    from web.glogging import RedactFilter

    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "%s %s", ("only one",), None)
    assert RedactFilter().filter(record) is True


def test_log_filter_redacts_tracebacks():
    import logging
    import sys

    from web.glogging import RedactFilter

    try:
        raise ValueError("bad path /account/magic/SECRETTOKEN")
    except ValueError:
        exc_info = sys.exc_info()
    record = logging.LogRecord("django.request", logging.ERROR, __file__, 1, "boom", (), exc_info)
    assert RedactFilter().filter(record) is True
    text = logging.Formatter().format(record)
    assert "SECRETTOKEN" not in text
    assert "/account/magic/…" in text

    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "boom", (), None)
    record.exc_text = "Traceback: /waitlist/confirm/SECRET"
    RedactFilter().filter(record)
    assert "SECRET" not in record.exc_text
