#!/bin/bash
# CI smoke test: delete a file to the freedesktop.org Trash through the API and restore it.
# Usage: smoke-test-restore.sh <junkzero binary> <port> <target folder>
set -euo pipefail
bin=$1; port=$2; target=$3
mkdir -p "$target/tmp"
file="$target/tmp/old.tmp"
echo junk > "$file"
touch -d "90 days ago" "$file"
out=$(mktemp)
"$bin" --mode server --port "$port" > "$out" 2>&1 &
server=$!
trap 'kill $server 2>/dev/null || true' EXIT
token=""
for i in $(seq 1 30); do
  token=$(grep -o 'token=[^ ]*' "$out" | head -1 | cut -d= -f2 || true)
  [ -n "$token" ] && curl -fsS "http://127.0.0.1:$port/api/system/info" >/dev/null 2>&1 && break
  sleep 1
done
[ -n "$token" ] || { echo "No token"; cat "$out"; exit 1; }
base="http://127.0.0.1:$port"
h="X-JunkZero-Token: $token"
curl -fsS "$base/api/system/info"; echo
curl -fsS -X POST "$base/api/scan/start" -H "$h" -H "Content-Type: application/json" \
  -d "{\"target_path\": \"$target\", \"scan_junk_locations\": false}" >/dev/null
for i in $(seq 1 60); do
  status=$(curl -fsS "$base/api/scan/status" -H "$h" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')
  [ "$status" != "running" ] && break
  sleep 1
done
result=$(curl -fsS -X POST "$base/api/clean" -H "$h" -H "Content-Type: application/json" \
  -d "{\"items\": [{\"path\": \"$file\"}], \"permanent\": false, \"confirm_text\": \"DELETE\"}")
echo "$result"
# Moved to the Trash (not erased, not set aside in JunkZero's holding folder)
echo "$result" | python3 -c 'import json,sys; r=json.load(sys.stdin); assert r["deleted_count"] == 1 and r["mode"] == "recycle_bin" and not r["held"], r'
test ! -e "$file" || { echo "Still there after the delete"; exit 1; }
stamp=$(curl -fsS "$base/api/history" -H "$h" | python3 -c 'import json,sys; print(json.load(sys.stdin)["history"][0]["timestamp"])')
curl -fsS -X POST "$base/api/history/restore" -H "$h" -H "Content-Type: application/json" -d "{\"timestamp\": $stamp}"; echo
test -f "$file" || { echo "Restore did not put it back"; exit 1; }
echo "Moved $file to the Trash and restored it"
