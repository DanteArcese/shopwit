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
  rotate_local
  log "kept local copy $name"

  if r2_configured; then
    r2_env
    rclone copyto "$file" "r2:${R2_BUCKET}/pg/$name"
    log "uploaded $name to r2:${R2_BUCKET}/pg/"
  else
    log "R2 not configured; kept dump locally"
  fi

  rm -f -- "$file"
  if [ -n "${BACKUP_HEARTBEAT_URL:-}" ]; then
    curl -fsS -m 10 --retry 3 -o /dev/null "$BACKUP_HEARTBEAT_URL"
    log "heartbeat sent"
  fi
}

seconds_until() {
  local now target
  now=$(date -u +%s)
  target=$(date -u -d "today ${BACKUP_AT:-03:30}" +%s)
  [ "$target" -le "$now" ] && target=$(date -u -d "tomorrow ${BACKUP_AT:-03:30}" +%s)
  echo $((target - now))
}

loop() {
  local wait
  while true; do
    wait=$(seconds_until)
    log "next run in ${wait}s (BACKUP_AT=${BACKUP_AT:-03:30} UTC)"
    sleep "$wait"
    if ! ( once ); then log "run FAILED"; fi
  done
}

restore_latest() {
  local url="${1:?usage: backup.sh restore-latest <target-db-url>}" latest
  if r2_configured; then
    r2_env
    latest=$(rclone lsf "r2:${R2_BUCKET}/pg/" | grep '^unitpricer-.*\.dump$' | sort | tail -n 1)
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
  restore-latest) shift; restore_latest "$@" ;;
  *) echo "usage: backup.sh once|loop|restore-latest <target-db-url>" >&2; exit 2 ;;
esac
