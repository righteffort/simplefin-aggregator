#!/usr/bin/env bash

# Manual, human-run end-to-end check against the real SimpleFIN demo bridge.
#
# This is NOT part of the automated test suite (pytest never makes real
# network calls). It exercises the full path against a live server: claims
# a SimpleFIN demo setup token, writes a throwaway config, starts the real
# aggregator, and queries /simplefin/info and /simplefin/accounts through
# it. Requires outbound network access.
#
# Get a fresh demo setup token from https://beta-bridge.simplefin.org/info/developers
# (the page mints a new one on every load -- it is not a fixed value), then run:
#
#   ./scripts/manual_verify.sh <demo-setup-token>

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <demo-setup-token>" >&2
  echo "  get one from https://beta-bridge.simplefin.org/info/developers" >&2
  exit 1
fi
SETUP_TOKEN="$1"

PORT=8321
CONFIG_FILE="$(mktemp)"
SERVER_PID=""

cleanup() {
  if [[ -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -f "$CONFIG_FILE"
}
trap cleanup EXIT

echo "==> Claiming the SimpleFIN demo setup token..."
ACCESS_URL="$(uv run simplefin-aggregator claim "$SETUP_TOKEN" 2>/dev/null)"
echo "    access URL claimed (not printed; it embeds credentials)"

cat >"$CONFIG_FILE" <<EOF
bind_host = "127.0.0.1"
bind_port = ${PORT}
base_url = "http://127.0.0.1:${PORT}"
claim_token = "manual-verify-claim-token"

[client]
username = "manual-verify"
password = "manual-verify-password"

[[providers]]
name = "demo"
access_url = "${ACCESS_URL}"
EOF
chmod 600 "$CONFIG_FILE"

echo "==> Starting simplefin-aggregator on 127.0.0.1:${PORT}..."
uv run simplefin-aggregator serve --config "$CONFIG_FILE" &
SERVER_PID=$!

echo "==> Waiting for the server to start listening..."
for _ in $(seq 1 50); do
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "server process exited before it started listening" >&2
    exit 1
  fi
  if curl -sS -o /dev/null "http://127.0.0.1:${PORT}/simplefin/info"; then
    break
  fi
  sleep 0.2
done

echo
echo "==> GET /simplefin/info"
curl -sS "http://127.0.0.1:${PORT}/simplefin/info"
echo
echo

echo "==> GET /simplefin/accounts"
curl -sS -u manual-verify:manual-verify-password "http://127.0.0.1:${PORT}/simplefin/accounts"
echo
echo

echo "==> GET /simplefin/accounts without credentials (expect 403)"
curl -sS -o /dev/null -w "HTTP %{http_code}\n" "http://127.0.0.1:${PORT}/simplefin/accounts"

echo
echo "==> Done."
