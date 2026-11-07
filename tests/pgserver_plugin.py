"""Pytest plugin giving the suite a real Postgres without Docker.

Named for the plan's original pgserver idea (no cp313 wheels exist), it now
uses system PostgreSQL binaries (`sudo apt install postgresql`). When
DATABASE_URL is unset it initdb's a throwaway cluster under `.pytest-pg/run-<pid>/`
(trust auth, localhost, free port), exports DATABASE_URL and DJANGO_DEBUG=1
before pytest-django configures Django, and stops and deletes the cluster at
session end. Per-run directories keep concurrent runs independent; leftovers
from crashed runs are cleaned at startup.
"""

import glob
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PG_ROOT = ROOT / ".pytest-pg"
_state = {}


def _bindir() -> Path:
    if shutil.which("pg_config"):
        out = subprocess.run(
            ["pg_config", "--bindir"], capture_output=True, text=True
        ).stdout.strip()
        if out and (Path(out) / "initdb").exists():
            return Path(out)

    def version(p):
        try:
            return int(Path(p).parent.name)
        except ValueError:
            return 0

    for p in sorted(glob.glob("/usr/lib/postgresql/*/bin"), key=version, reverse=True):
        if (Path(p) / "initdb").exists():
            return Path(p)
    initdb = shutil.which("initdb")
    if initdb:
        return Path(initdb).parent
    raise pytest.UsageError(
        "PostgreSQL binary 'initdb' not found (also checked pg_config and "
        "/usr/lib/postgresql/*/bin). Install it with `sudo apt install postgresql` "
        "or set DATABASE_URL to an existing database."
    )


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _clean_stale(bindir: Path):
    for d in PG_ROOT.glob("run-*"):
        try:
            pid = int(d.name.split("-", 1)[1])
        except ValueError:
            continue
        if _alive(pid):
            continue
        if (d / "data" / "postmaster.pid").exists():
            subprocess.run(
                [
                    str(bindir / "pg_ctl"),
                    "-D",
                    str(d / "data"),
                    "-m",
                    "immediate",
                    "stop",
                ],
                capture_output=True,
            )
        shutil.rmtree(d, ignore_errors=True)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start() -> str:
    bindir = _bindir()
    for tool in ("pg_ctl",):
        if not (bindir / tool).exists():
            raise pytest.UsageError(f"PostgreSQL binary '{tool}' not found in {bindir}")
    PG_ROOT.mkdir(exist_ok=True)
    _clean_stale(bindir)
    run = PG_ROOT / f"run-{os.getpid()}"
    data = run / "data"
    run.mkdir()
    port = _free_port()
    try:
        subprocess.run(
            [
                str(bindir / "initdb"),
                "-D",
                str(data),
                "-U",
                "postgres",
                "--auth=trust",
                "-E",
                "UTF8",
                "--no-sync",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                str(bindir / "pg_ctl"),
                "-D",
                str(data),
                "-l",
                str(run / "log"),
                "-w",
                "-o",
                f"-p {port} -c listen_addresses=127.0.0.1 "
                f"-c unix_socket_directories='' -c fsync=off",
                "start",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        shutil.rmtree(run, ignore_errors=True)
        raise pytest.UsageError(
            f"Could not start test Postgres: {e.stderr or e.stdout}"
        )
    _state.update(bindir=bindir, run=run, data=data)
    return f"postgres://postgres@127.0.0.1:{port}/postgres"


@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(early_config, parser, args):
    os.environ["DJANGO_DEBUG"] = "1"
    if not os.environ.get("DATABASE_URL"):
        os.environ["DATABASE_URL"] = _start()


def pytest_unconfigure(config):
    if "run" in _state:
        subprocess.run(
            [
                str(_state["bindir"] / "pg_ctl"),
                "-D",
                str(_state["data"]),
                "-m",
                "fast",
                "stop",
            ],
            capture_output=True,
        )
        shutil.rmtree(_state["run"], ignore_errors=True)
        _state.clear()
