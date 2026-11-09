import os
import stat
import subprocess

import pytest

from .conftest import ROOT

SCRIPT = ROOT / "deploy" / "deploy.sh"
SHA = "a" * 40

FAKES = {
    "git": 'echo "git $*" >> "$FAKE_LOG"; case "$1" in rev-parse) echo ' + SHA + ' ;; show) echo "compose-body" ;; esac',
    "gh": 'echo "gh $*" >> "$FAKE_LOG"; echo "${FAKE_GH_GREEN:-0}"',
    "ssh": 'echo "ssh $*" >> "$FAKE_LOG"; echo "---stdin---" >> "$FAKE_LOG"; cat >> "$FAKE_LOG"',
}


@pytest.fixture
def run(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in FAKES.items():
        p = bindir / name
        p.write_text(f"#!/bin/bash\n{body}\n")
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "log"
    log.touch()

    def go(green="1", host="deploy@203.0.113.7", *args, build=False):
        env = {"PATH": f"{bindir}:{os.environ['PATH']}", "HOME": str(tmp_path),
               "FAKE_LOG": str(log), "FAKE_GH_GREEN": green}
        if build:
            env["UNITPRICER_BUILD_ON_SERVER"] = "1"
        if host:
            env["UNITPRICER_DEPLOY_HOST"] = host
        proc = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True)
        return proc, log.read_text()

    return go


def test_script_is_executable():
    assert SCRIPT.stat().st_mode & stat.S_IXUSR


def test_requires_deploy_host(run):
    proc, log = run("1", "")
    assert proc.returncode != 0
    assert "UNITPRICER_DEPLOY_HOST" in proc.stderr
    assert "ssh" not in log


def test_refuses_without_green_ci(run):
    proc, log = run("0")
    assert proc.returncode != 0
    assert "ssh" not in log
    assert "--workflow CI" in log and "--branch main" in log


def _script_line(log):
    return next(line for line in log.splitlines() if line.startswith("ssh") and "manage.py migrate" in line)


def _order(text, needles):
    idx = [text.index(n) for n in needles]
    assert idx == sorted(idx), idx


def test_orders_pull_migrate_up(run):
    proc, log = run("1")
    assert proc.returncode == 0, proc.stderr
    assert "-o IdentitiesOnly=yes" in log
    assert "git fetch" in log and f"git show {SHA}:compose.prod.yaml" in log
    line = _script_line(log)
    assert f"sha-{SHA}" in line and "umask 077" in line
    _order(line, ["compose pull", "manage.py check --deploy", "manage.py migrate", "sync_categories", "createcachetable", "up -d"])
    assert "docker build" not in line
    assert SHA in proc.stdout


def test_remote_script_is_argument_not_stdin_and_run_uses_T(run):
    proc, log = run("1")
    line = _script_line(log)
    assert line.count("compose run") == 4
    assert line.count("compose run --rm -T") == 4
    stdin_after = log.split(line, 1)[1].split("---stdin---", 1)[1]
    assert "migrate" not in stdin_after


def test_build_on_server_mode_order(run):
    proc, log = run("1", build=True)
    assert proc.returncode == 0, proc.stderr
    line = _script_line(log)
    assert "compose pull db cloudflared" in line
    assert "compose pull\n" not in line
    _order(line, [f"docker build -t ghcr.io/dantearcese/unitpricer:sha-{SHA}",
                  f"unitpricer-backup:sha-{SHA}", "compose pull db cloudflared",
                  "manage.py check --deploy", "manage.py migrate", "sync_categories", "createcachetable", "up -d"])


def test_category_sync_runs_after_migrate(run):
    proc, log = run("1")
    line = _script_line(log)
    assert "compose run --rm -T web python manage.py sync_categories </dev/null" in line
    _order(line, ["manage.py migrate --noinput", "manage.py sync_categories", "up -d"])


def test_deploy_check_runs_before_migrate_and_aborts(run):
    proc, log = run("1")
    line = _script_line(log)
    assert "compose run --rm -T web python manage.py check --deploy --fail-level ERROR </dev/null" in line
    # set -e in the remote script: a failing check stops before migrate.
    assert line.index("set -euo pipefail") < line.index("manage.py check --deploy")
