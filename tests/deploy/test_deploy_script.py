import os
import stat
import subprocess

import pytest

from .conftest import ROOT

SCRIPT = ROOT / "deploy" / "deploy.sh"
SHA = "a" * 40

FAKES = {
    "git": 'echo "git $*" >> "$FAKE_LOG"; case "$1" in rev-parse) echo ' + SHA + ' ;; esac',
    "gh": 'echo "gh $*" >> "$FAKE_LOG"; echo "${FAKE_GH_GREEN:-0}"',
    "scp": 'echo "scp $*" >> "$FAKE_LOG"',
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

    def go(green="1", host="deploy@203.0.113.7", *args):
        env = {"PATH": f"{bindir}:{os.environ['PATH']}", "HOME": str(tmp_path),
               "FAKE_LOG": str(log), "FAKE_GH_GREEN": green}
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
    assert "scp" not in log and "ssh" not in log
    assert "--workflow CI" in log


def test_orders_pull_migrate_up(run):
    proc, log = run("1")
    assert proc.returncode == 0, proc.stderr
    assert "-o IdentitiesOnly=yes" in log
    remote = log.split("---stdin---", 1)[1]
    assert f"sha-{SHA}" in remote
    idx = [remote.index(s) for s in ("compose pull", "manage.py migrate", "createcachetable", "up -d")]
    assert idx == sorted(idx)
    assert SHA in proc.stdout
