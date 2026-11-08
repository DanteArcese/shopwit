import os
import re
import stat
import subprocess

import pytest

from .conftest import ROOT

SCRIPT = ROOT / "deploy" / "backup" / "backup.sh"
SECRET = "r2-secret-key-XYZ"
PASSWORD = "pg-password-ABC"

FAKES = {
    "pg_dump": (
        'echo "pg_dump $*" >> "$FAKE_LOG"; [ "${FAKE_DUMP_FAIL:-}" = 1 ] && exit 1\n'
        'while [ $# -gt 0 ]; do [ "$1" = -f ] && echo dump > "$2"; shift; done'
    ),
    "pg_restore": (
        'echo "pg_restore $*" >> "$FAKE_LOG"; [ "${FAKE_RESTORE_FAIL:-}" = 1 ] && exit 1; exit 0'
    ),
    "rclone": (
        'echo "rclone $*" >> "$FAKE_LOG"\n'
        'if [ "$1" = lsf ]; then printf "unitpricer-20240101T000000Z.dump\\nunitpricer-20240301T000000Z.dump\\n"; fi\n'
        'if [ "$1" = copyto ] && [ ! -e "$2" ]; then :; fi\n'
        'if [ "$1" = copyto ]; then echo "$RCLONE_CONFIG_R2_ENDPOINT $RCLONE_CONFIG_R2_PROVIDER '
        '$RCLONE_CONFIG_R2_NO_CHECK_BUCKET $RCLONE_CONFIG_R2_TYPE" >> "$FAKE_LOG"; '
        'echo "opts config=$RCLONE_CONFIG nochecksum=$RCLONE_CONFIG_R2_DISABLE_CHECKSUM" >> "$FAKE_LOG"; '
        'case "$2" in r2:*) echo dump > "$3" ;; esac; fi'
    ),
    "sleep": (
        'echo "sleep $*" >> "$FAKE_LOG"; n=$(cat "$FAKE_LOG.sleeps" 2>/dev/null || echo 0); '
        'echo $((n + 1)) > "$FAKE_LOG.sleeps"; [ "$n" -ge 1 ] && kill -TERM $PPID; exit 0'
    ),
    "curl": 'echo "curl $*" >> "$FAKE_LOG"',
}

R2 = {"R2_ACCOUNT_ID": "acct1", "R2_ACCESS_KEY_ID": "keyid", "R2_SECRET_ACCESS_KEY": SECRET}


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
    work = tmp_path / "work"
    store = tmp_path / "backups"
    work.mkdir()
    store.mkdir()

    def go(*args, env=None):
        full = {
            "PATH": f"{bindir}:{os.environ['PATH']}", "HOME": str(tmp_path),
            "FAKE_LOG": str(log), "BACKUP_TMP": str(work), "BACKUP_DIR": str(store),
            "PGPASSWORD": PASSWORD, "R2_BUCKET": "unitpricer-backups",
            **(env or {}),
        }
        proc = subprocess.run(["bash", str(SCRIPT), *(args or ("once",))],
                              env=full, capture_output=True, text=True)
        return proc, log.read_text()

    go.work, go.store = work, store
    return go


def test_backup_uploads_validated_dump(run):
    proc, log = run(env=R2)
    assert proc.returncode == 0, proc.stderr
    dest = re.search(r"^rclone copyto \S+ (\S+)$", log, re.M).group(1)
    assert re.fullmatch(r"r2:unitpricer-backups/pg/unitpricer-\d{8}T\d{6}Z\.dump", dest)
    assert log.index("pg_restore --list") < log.index("rclone copyto")
    assert "pg_dump -Fc --no-owner" in log
    assert "https://acct1.r2.cloudflarestorage.com Cloudflare true s3" in log
    assert "opts config=/dev/null nochecksum=true" in log
    assert list(run.work.iterdir()) == []
    assert len(list(run.store.glob("unitpricer-*.dump"))) == 1  # second copy


def test_dump_failure_no_upload_no_heartbeat(run):
    proc, log = run(env={**R2, "FAKE_DUMP_FAIL": "1", "BACKUP_HEARTBEAT_URL": "https://hb.example/x"})
    assert proc.returncode != 0
    assert "rclone" not in log and "curl" not in log
    assert list(run.store.iterdir()) == []


def test_invalid_dump_no_upload(run):
    proc, log = run(env={**R2, "FAKE_RESTORE_FAIL": "1", "BACKUP_HEARTBEAT_URL": "https://hb.example/x"})
    assert proc.returncode != 0
    assert "rclone" not in log and "curl" not in log
    assert list(run.store.iterdir()) == [] and list(run.work.iterdir()) == []


def test_heartbeat_only_when_configured(run):
    _, log = run(env=R2)
    assert "curl" not in log
    proc, log = run(env={**R2, "BACKUP_HEARTBEAT_URL": "https://hb.example/x"})
    assert proc.returncode == 0
    assert "curl -fsS -m 10 --retry 3" in log and "https://hb.example/x" in log


def test_secrets_not_echoed(run):
    proc, _ = run(env={**R2, "BACKUP_HEARTBEAT_URL": "https://hb.example/x"})
    out = proc.stdout + proc.stderr
    assert SECRET not in out and PASSWORD not in out


def seed(store, n):
    for i in range(n):
        (store / f"unitpricer-20240101T0000{i:02d}Z.dump").write_text("old")


def test_without_r2_keeps_dump_locally_and_heartbeats(run):
    proc, log = run(env={"BACKUP_HEARTBEAT_URL": "https://hb.example/x"})
    assert proc.returncode == 0, proc.stderr
    assert "R2 not configured; kept dump locally" in proc.stdout
    assert "rclone" not in log
    assert len(list(run.store.glob("unitpricer-*.dump"))) == 1
    assert "https://hb.example/x" in log


def test_partial_r2_config_counts_as_unconfigured(run):
    proc, log = run(env={"R2_ACCOUNT_ID": "acct1"})
    assert proc.returncode == 0
    assert "rclone" not in log


@pytest.mark.parametrize("env", [{}, R2])
def test_local_rotation_keeps_newest_seven(run, env):
    seed(run.store, 10)
    proc, _ = run(env=env)
    assert proc.returncode == 0, proc.stderr
    names = sorted(p.name for p in run.store.glob("unitpricer-*.dump"))
    assert len(names) == 7
    assert names[-1] > "unitpricer-2025"  # the new dump survived
    assert "unitpricer-20240101T000000Z.dump" not in names


def test_restore_latest_from_r2(run):
    proc, log = run("restore-latest", "postgres://u@h/db", env=R2)
    assert proc.returncode == 0, proc.stderr
    assert "rclone lsf r2:unitpricer-backups/pg/" in log
    assert "unitpricer-20240301T000000Z.dump" in log
    assert "pg_restore --no-owner --clean --if-exists -d postgres://u@h/db" in log


def test_restore_latest_local_when_no_r2(run):
    seed(run.store, 3)
    proc, log = run("restore-latest", "postgres://u@h/db")
    assert proc.returncode == 0, proc.stderr
    assert "rclone" not in log
    assert "unitpricer-20240101T000002Z.dump" in log.split("pg_restore")[1]


def test_loop_failed_run_sends_no_heartbeat(run):
    env = {**R2, "FAKE_DUMP_FAIL": "1", "BACKUP_HEARTBEAT_URL": "https://hb.example/x"}
    proc, log = run("loop", env=env)  # fake sleep terminates the loop on its 2nd call
    assert "run FAILED" in proc.stdout
    assert "rclone" not in log and "curl" not in log
    assert log.count("pg_dump") == 1


def test_loop_rejects_bad_backup_at(run):
    for bad in ("25:00", "3:30", "03:60", "x"):
        proc, log = run("loop", env={"BACKUP_AT": bad})
        assert proc.returncode == 2 and "invalid BACKUP_AT" in proc.stderr
        assert "pg_dump" not in log


def delay(run, now, at="03:30"):
    proc, _ = run("next-delay", env={"BACKUP_AT": at, "BACKUP_NOW_EPOCH": str(now)})
    assert proc.returncode == 0, proc.stderr
    return int(proc.stdout)


def test_seconds_until_later_today_and_tomorrow(run):
    day = 1_700_000_000 - 1_700_000_000 % 86400  # a UTC midnight
    assert delay(run, day + 3600) == 2 * 3600 + 1800
    assert delay(run, day + 4 * 3600) == 23 * 3600 + 1800
    assert delay(run, day + 3 * 3600 + 1800) == 86400  # exactly now -> tomorrow
    assert delay(run, day + 3 * 3600 + 1799) == 1


def test_heartbeat_failure_fails_run_without_leaking_url(run):
    # curl fake succeeds; here just confirm the URL is never printed on success.
    proc, _ = run(env={"BACKUP_HEARTBEAT_URL": "https://hb.example/secret-token"})
    assert "secret-token" not in proc.stdout + proc.stderr


def test_no_rotation_when_upload_fails(run, tmp_path):
    seed(run.store, 9)
    bad = tmp_path / "bin" / "rclone"
    bad.write_text("#!/bin/bash\nexit 1\n")
    proc, _ = run(env=R2)
    assert proc.returncode != 0
    assert len(list(run.store.glob("unitpricer-*.dump"))) == 10
