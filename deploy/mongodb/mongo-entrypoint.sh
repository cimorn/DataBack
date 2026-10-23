#!/bin/bash
# Start the FINAL authenticated replica-set daemon as the Compose-selected user.
set -Eeuo pipefail
umask 077

fail() {
  printf '%s\n' "MongoDB startup failed; check dedicated data permissions, keyfile and private configuration." >&2
  exit 1
}
trap fail ERR
[[ "$(id -u):$(id -g)" == "1000:10" ]] || fail
[[ -d /data/db && -w /data/db ]] || fail
mkdir -p "${HOME:-/tmp/databack-mongosh}"
keyfile=/data/db/.databack-keyfile
if [[ ! -f "$keyfile" ]]; then
  temporary=$(mktemp /data/db/.databack-keyfile.XXXXXX)
  # 756 random bytes -> 1008 base64 characters, below MongoDB's 1024 limit.
  head -c 756 /dev/urandom | base64 -w 0 > "$temporary"
  chmod 600 "$temporary"
  mv "$temporary" "$keyfile"
fi
[[ ! -L "$keyfile" && "$(stat -c '%u' "$keyfile")" == "1000" ]] || fail
chmod 600 "$keyfile"

# Suppress daemon diagnostics that can contain authentication identities.
# The wrapper/bootstrap emit only fixed phase/status messages.
# Use the default console logger so Mongo never opens or rotates a log file.
mongod --dbpath /data/db --bind_ip_all --port 27017 --replSet databack \
  --auth --keyFile "$keyfile" > /dev/null 2>&1 &
daemon=$!
shutdown() {
  kill -TERM "$daemon" 2>/dev/null || true
  wait "$daemon" 2>/dev/null || true
}
trap shutdown TERM INT
if ! mongosh --nodb --quiet --file /opt/databack/mongo-bootstrap.js; then
  shutdown
  fail
fi
printf '%s\n' "MongoDB authenticated replica set ready."
set +e
wait "$daemon"
status=$?
if [[ "$status" -ne 0 ]]; then
  printf '%s\n' "MongoDB daemon stopped; inspect dedicated data permissions and configuration." >&2
fi
exit "$status"
