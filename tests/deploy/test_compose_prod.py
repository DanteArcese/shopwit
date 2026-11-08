import yaml

from .conftest import ROOT

COMPOSE = yaml.safe_load((ROOT / "compose.prod.yaml").read_text())
SERVICES = COMPOSE["services"]


def _env_keys(svc):
    env = svc.get("environment", {})
    return set(env) if isinstance(env, dict) else {e.split("=", 1)[0] for e in env}


def test_no_published_ports_or_builds():
    for name, svc in SERVICES.items():
        assert "ports" not in svc, name
        assert "build" not in svc, name


def test_all_services_restart():
    assert set(SERVICES) == {"db", "web", "worker", "cloudflared", "backup"}
    for name, svc in SERVICES.items():
        assert svc["restart"] == "unless-stopped", name


def test_images_pinned():
    assert SERVICES["db"]["image"] == "postgres:17"
    for name in ("web", "worker"):
        assert SERVICES[name]["image"] == "ghcr.io/dantearcese/unitpricer:${UNITPRICER_IMAGE_TAG:?}"
    assert SERVICES["backup"]["image"] == "ghcr.io/dantearcese/unitpricer-backup:${UNITPRICER_IMAGE_TAG:?}"
    tag = SERVICES["cloudflared"]["image"].split(":", 1)
    assert tag[0] == "cloudflare/cloudflared" and tag[1] not in ("", "latest")


def test_secrets_scoped():
    assert _env_keys(SERVICES["cloudflared"]) == {"TUNNEL_TOKEN"}
    assert "env_file" not in SERVICES["cloudflared"]
    for name in ("web", "worker"):
        ef = SERVICES[name]["env_file"]
        assert (ef if isinstance(ef, list) else [ef]) == ["app.env"]
    for name, svc in SERVICES.items():
        assert not ("TUNNEL_TOKEN" in _env_keys(svc) and "env_file" in svc), name


def test_web_healthcheck_hits_healthz_with_apex_host():
    hc = SERVICES["web"]["healthcheck"]
    cmd = " ".join(hc["test"])
    assert "http://127.0.0.1:8000/healthz" in cmd
    assert "Host" in cmd and "unitpricer.com" in cmd
    assert hc["interval"] == "30s" and hc["start_period"] == "20s"


def test_backup_and_worker_wiring():
    assert SERVICES["worker"]["command"] == "python manage.py run_scheduler"
    assert SERVICES["worker"]["stop_grace_period"] == "60s"
    backup = SERVICES["backup"]
    assert "env_file" not in backup
    assert backup["environment"]["R2_ACCOUNT_ID"] == "${R2_ACCOUNT_ID:-}"
    assert any(v.startswith("backups:/backups") for v in backup["volumes"])
    assert {"pgdata", "backups"} <= set(COMPOSE["volumes"])
