#!/usr/bin/env bash
# Nightly pg_dump. Modes: once | loop | restore-latest <target-db-url>.
# With R2_* set: upload to r2:$R2_BUCKET/pg/ (bucket lifecycle prunes after 30 days).
# Always keep the newest $KEEP_LOCAL dumps in $BACKUP_DIR (the only copy until R2 is on).
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/backups}"
WORK_DIR="${BACKUP_TMP:-/tmp}"
file=""
KEEP_LOCAL=7
R2_BUCKET="${R2_BUCKET:-unitpricer-backups}"

log() { echo "backup: $*"; }

r2_configured() {
  [ -n "${R2_ACCOUNT_ID:-}" ] && [ -n "${R2_ACCESS_KEY_ID:-}" ] && [ -n "${R2_SECRET_ACCESS_KEY:-}" ]
}

r2_env() {
  export RCLONE_CONFIG_R2_TYPE=s3
  export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
  export RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
  export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
  export RCLONE_CONFIG_R2_ENDPOINT="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
  export RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true
}

rotate_local() {
  local f
  # Names embed a sortable UTC timestamp; everything after the newest 7 goes.
  ls -1 "$BACKUP_DIR"/unitpricer-*.dump 2>/dev/null | sort -r | tail -n +"$((KEEP_LOCAL + 1))" |
    while IFS= read -r f; do rm -f -- "$f"; log "rotated out $(basename "$f")"; done
}

once() {
  local name
  name="unitpricer-$(date -u +%Y%m%dT%H%M%SZ).dump"
  file="$WORK_DIR/$name"
  trap '[ -z "$file" ] || rm -f -- "$file"' EXIT

  pg_dump -Fc --no-owner -f "$file"
  log "dumped $name"
  pg_restore --list "$file" > /dev/null
  log "validated $name"

  mkdir -p "$BACKUP_DIR"
  cp -- "$file" "$BACKUP_DIR/$name"
  log "kept local copy $name"

  if r2_configured; then
    r2_env
    rclone copyto "$file" "r2:${R2_BUCKET}/pg/$name"
    log "uploaded $name to r2:${R2_BUCKET}/pg/"
  else
    log "R2 not configured; kept dump locally"
  fi
  rotate_local

  rm -f -- "$file"
  if [ -n "${BACKUP_HEARTBEAT_URL:-}" ]; then
    curl -fsS -m 10 --retry 3 "$BACKUP_HEARTBEAT_URL" > /dev/null 2>&1 || { log "heartbeat failed"; exit 1; }
    log "heartbeat sent"
  fi
}

valid_backup_at() {
  [[ "${BACKUP_AT:-03:30}" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]
}

# Seconds until the next BACKUP_AT (UTC), always >= 1. BACKUP_NOW_EPOCH is a test hook.
seconds_until() {
  local now day target at="${BACKUP_AT:-03:30}"
  now="${BACKUP_NOW_EPOCH:-$(date -u +%s)}"
  day=$(date -u -d "@$now" +%Y-%m-%d)
  target=$(date -u -d "$day $at" +%s)
  [ "$target" -le "$now" ] && target=$((target + 86400))
  echo $((target - now))
}

loop() {
  local delay
  if ! valid_backup_at; then
    echo "backup: invalid BACKUP_AT '${BACKUP_AT}' (want HH:MM, 24h UTC)" >&2
    exit 2
  fi
  while true; do
    delay=$(seconds_until)
    log "next run in ${delay}s (BACKUP_AT=${BACKUP_AT:-03:30} UTC)"
    sleep "$delay"
    # Separate process so set -e stays active inside the run.
    "$0" once || log "run FAILED"
  done
}

restore_latest() {
  local url="${1:?usage: backup.sh restore-latest <target-db-url>}" latest
  if r2_configured; then
    r2_env
    latest=$(rclone lsf "r2:${R2_BUCKET}/pg/" | grep '^unitpricer-.*\.dump$' | sort | tail -n 1 || true)
    [ -n "$latest" ] || { log "no dumps in r2:${R2_BUCKET}/pg/"; return 1; }
    file="$WORK_DIR/$latest"
    trap '[ -z "$file" ] || rm -f -- "$file"' EXIT
    rclone copyto "r2:${R2_BUCKET}/pg/$latest" "$file"
  else
    latest=$(ls -1 "$BACKUP_DIR"/unitpricer-*.dump 2>/dev/null | sort | tail -n 1 || true)
    [ -n "$latest" ] || { log "no local dumps in $BACKUP_DIR"; return 1; }
    file="$latest"
  fi
  log "restoring $(basename "$latest")"
  pg_restore --no-owner --clean --if-exists -d "$url" "$file"
  log "restore finished"
}

case "${1:-loop}" in
  once) once ;;
  loop) loop ;;
  next-delay) valid_backup_at && seconds_until ;;
  restore-latest) shift; restore_latest "$@" ;;
  *) echo "usage: backup.sh once|loop|restore-latest <target-db-url>" >&2; exit 2 ;;
esac
