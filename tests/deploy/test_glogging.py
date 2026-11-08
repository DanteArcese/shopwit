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
