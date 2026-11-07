#!/usr/bin/env bash
# deploy.sh <digest> — deploy ghcr.io/bullionbear/cpanel@<digest> on this host (C2-07, C2-D003).
#
#   1. pull the image;
#   2. run cpanel-migrate up; if it fails, stop here: the running release is untouched;
#   3. replace cpanel-api and cpanel-worker, then wait for cpanel-api /readyz and cpanel-worker /healthz;
#   4. if they do not pass, put the previous digest back and exit 1.
#
# The current and previous digests are in state/current and state/previous next to this script.
# `deploy.sh --rollback` deploys state/previous without migrating.
#
# Rolling back works only when the failed release added no migration: an older binary refuses a database
# with a migration it does not know (migrations.RequireCurrent). Then the fix is forward. docs/operations.md.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
STATE="$DIR/state"
REPO="ghcr.io/bullionbear/cpanel"
READY_TIMEOUT="${READY_TIMEOUT:-90}"
compose() { docker compose -p cpanel -f "$DIR/compose.yml" "$@"; }
say() { printf '%s deploy: %s\n' "$(date -u +%H:%M:%SZ)" "$*" >&2; }

usage() { echo "usage: $0 sha256:<64 hex> | --rollback" >&2; exit 2; }
[ $# -eq 1 ] || usage

mkdir -p "$STATE"
exec 9>"$STATE/lock"
flock -n 9 || { say "another deploy is running"; exit 1; }

current="$(cat "$STATE/current" 2>/dev/null || true)"
previous="$(cat "$STATE/previous" 2>/dev/null || true)"

migrate=1
case "$1" in
  --rollback)
    [ -n "$previous" ] || { say "no previous digest in $STATE/previous"; exit 1; }
    digest="$previous"; migrate=0 ;;
  sha256:*) digest="$1" ;;
  *) usage ;;
esac
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || usage

for f in database_url discord_client_secret discord_bot_token mftiko_token sym_token; do
  [ -s "$DIR/secrets/$f" ] || { say "missing secret $DIR/secrets/$f"; exit 1; }
done
[ -f "$DIR/config.env" ] || { say "missing $DIR/config.env"; exit 1; }

# container_ip <name> <network>
container_ip() {
  docker inspect -f "{{with index .NetworkSettings.Networks \"$2\"}}{{.IPAddress}}{{end}}" "$1" 2>/dev/null || true
}

# ready waits until cpanel-api /readyz and cpanel-worker /healthz both answer 200, or READY_TIMEOUT passes.
ready() {
  local deadline=$((SECONDS + READY_TIMEOUT)) api worker
  while [ $SECONDS -lt $deadline ]; do
    api="$(container_ip cpanel-api web_proxy)"
    worker="$(container_ip cpanel-worker cpanel_default)"
    if [ -n "$api" ] && [ -n "$worker" ] &&
       curl -fsS -o /dev/null --max-time 3 "http://$api:8080/readyz" &&
       curl -fsS -o /dev/null --max-time 3 "http://$worker:8081/healthz"; then
      return 0
    fi
    sleep 2
  done
  return 1
}

# switch_to <digest>: replace cpanel-api and cpanel-worker with that image.
switch_to() {
  CPANEL_IMAGE="$REPO@$1" compose up -d --no-build --pull never --remove-orphans cpanel-api cpanel-worker
}

say "pull $REPO@$digest"
docker pull -q "$REPO@$digest" >/dev/null

if [ "$migrate" = 1 ]; then
  say "migrate"
  if ! CPANEL_IMAGE="$REPO@$digest" compose run --rm --no-deps cpanel-migrate; then
    say "migrate failed; ${current:-nothing} keeps running"
    exit 1
  fi
fi

say "switch ${current:-none} -> $digest"
switch_to "$digest"
if ready; then
  if [ "$digest" != "$current" ]; then
    [ -n "$current" ] && printf '%s\n' "$current" >"$STATE/previous"
    printf '%s\n' "$digest" >"$STATE/current"
  fi
  say "ready: $digest"
  exit 0
fi

say "not ready after ${READY_TIMEOUT}s"
CPANEL_IMAGE="$REPO@$digest" compose logs --no-color --tail 30 cpanel-api cpanel-worker >&2 || true
if [ -z "$current" ] || [ "$current" = "$digest" ]; then
  say "no other digest to go back to"
  exit 1
fi
say "roll back to $current"
switch_to "$current"
if ready; then
  say "rolled back: $current is serving"
else
  say "rollback is not ready either; if $digest added a migration, the old binary refuses the schema: fix forward"
fi
exit 1
