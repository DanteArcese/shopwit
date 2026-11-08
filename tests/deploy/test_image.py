from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_dockerfile_runs_gunicorn_as_non_root():
    lines = (ROOT / "Dockerfile").read_text().splitlines()
    cmd = next(line for line in lines if line.startswith("CMD"))
    assert "gunicorn" in cmd and "runserver" not in cmd
    assert any("collectstatic" in line for line in lines)
    last_run = max(i for i, line in enumerate(lines) if line.startswith("RUN"))
    assert any(line.strip() == "USER app" for line in lines[last_run:])
    assert "10001" in (ROOT / "Dockerfile").read_text()


def test_whitenoise_after_security_middleware():
    from django.conf import settings

    mw = settings.MIDDLEWARE
    i = mw.index("django.middleware.security.SecurityMiddleware")
    assert mw[i + 1] == "whitenoise.middleware.WhiteNoiseMiddleware"
    assert mw.index("web.hosts.HostRoutingMiddleware") > i + 1


def test_static_served_by_whitenoise_in_prod(prod_env, run_prod, tmp_path):
    env = {**prod_env, "DJANGO_STATIC_ROOT": str(tmp_path)}
    r = run_prod(env, "manage.py", "collectstatic", "--noinput")
    assert r.returncode == 0, r.stderr
    code = (
        "import django; django.setup()\n"
        "from django.contrib.staticfiles.storage import staticfiles_storage as s\n"
        "from django.test import Client\n"
        "url = s.url('web/app.css')\n"
        "r = Client().get(url, HTTP_HOST='unitpricer.com', secure=True)\n"
        "print(r.status_code, r.headers.get('Cache-Control'))\n"
    )
    env["DJANGO_SETTINGS_MODULE"] = "config.settings"
    r = run_prod(env, "-c", code)
    assert r.returncode == 0, r.stderr
    status, cache = r.stdout.strip().split(" ", 1)
    assert status == "200"
    assert "max-age=315360000" in cache


def test_gunicorn_access_log_has_no_ip():
    ns = {}
    exec((ROOT / "deploy" / "gunicorn.conf.py").read_text(), ns)
    fmt = ns["access_log_format"]
    for token in ("%(h)s", "%(a)s", "%(q)s"):
        assert token not in fmt
    assert ns["bind"] == "0.0.0.0:8000" and ns["timeout"] == 30


def test_ci_pushes_only_from_main():
    wf = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    job = wf["jobs"]["image"]
    assert job["needs"] == "test" or job["needs"] == ["test"]
    assert job["permissions"]["packages"] == "write"
    pushes = [s for s in job["steps"] if "ghcr.io/dantearcese/unitpricer" in str(s)]
    assert pushes
    for s in pushes:
        cond = s.get("if", "")
        assert "refs/heads/main" in cond and "push" in cond
    assert "ghcr.io/dantearcese/unitpricer" in (ROOT / ".github/workflows/ci.yml").read_text()


def test_ci_builds_and_pushes_backup_image_from_main_only():
    wf = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = wf["jobs"]["image"]["steps"]
    builds = [s for s in steps if s.get("with", {}).get("context") == "deploy/backup"]
    assert len(builds) == 2
    ci_build = [s for s in builds if not s["with"].get("push")]
    push = [s for s in builds if s["with"].get("push")]
    assert ci_build and "unitpricer-backup:ci" in ci_build[0]["with"]["tags"]
    assert "if" not in ci_build[0]
    tags = push[0]["with"]["tags"]
    assert "ghcr.io/dantearcese/unitpricer-backup:sha-${{ github.sha }}" in tags
    assert "ghcr.io/dantearcese/unitpricer-backup:main" in tags
    assert "refs/heads/main" in push[0]["if"] and "push" in push[0]["if"]
