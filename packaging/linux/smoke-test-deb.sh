#!/bin/bash
# CI smoke test: install the .deb, schedule a scan from the app, uninstall, and check the
# schedule is gone while the user's own cron job stays. Usage: smoke-test-deb.sh <deb file>
# (needs root or sudo; uses the current user's crontab)
set -euo pipefail
deb=$1
SUDO=$([ "$(id -u)" = 0 ] && echo "" || echo sudo)
case "$deb" in /*) ;; *) deb="./$deb" ;; esac
$SUDO apt-get install -y "$deb"
test -x /opt/junkzero/JunkZero && test -L /usr/bin/junkzero
grep -q '^Exec=junkzero$' /usr/share/applications/junkzero.desktop
test -f /usr/share/icons/hicolor/256x256/apps/junkzero.png
junkzero --version
# The user's own crontab line must survive the uninstall
(crontab -l 2>/dev/null || true; echo "0 3 * * * echo my own job") | crontab -
port=8797
out=$(mktemp)
junkzero --mode server --port $port > "$out" 2>&1 &
server=$!
token=""
for i in $(seq 1 30); do
  token=$(grep -o 'token=[^ ]*' "$out" | head -1 | cut -d= -f2 || true)
  [ -n "$token" ] && curl -fsS "http://127.0.0.1:$port/api/system/info" >/dev/null 2>&1 && break
  sleep 1
done
info=$(curl -fsS "http://127.0.0.1:$port/api/system/info"); echo "$info"
echo "$info" | grep -q '"platform":"linux"'
# The .deb has its own menu entry, so the installed copy offers none
shortcuts=$(curl -fsS "http://127.0.0.1:$port/api/shortcuts" -H "X-JunkZero-Token: $token"); echo "$shortcuts"
echo "$shortcuts" | grep -q '"supported":false'
curl -fsS -X PUT "http://127.0.0.1:$port/api/schedule" -H "X-JunkZero-Token: $token" -H "Content-Type: application/json" \
  -d '{"frequency": "daily", "time": "09:00", "day": "MON", "paths": [], "include_junk_locations": true}'; echo
kill $server
units="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
if crontab -l | grep -q '# JunkZero scheduled scan$'; then
  crontab -l | grep '# JunkZero scheduled scan$' | grep -q '/opt/junkzero/JunkZero --mode report'
  echo "Scheduled with cron: $(crontab -l | grep 'JunkZero scheduled scan')"
elif [ -f "$units/junkzero-scan.timer" ]; then
  grep -q '/opt/junkzero/JunkZero' "$units/junkzero-scan.service"
  echo "Scheduled with a systemd user timer"
else
  echo "No schedule was made"; exit 1
fi
$SUDO apt-get remove -y junkzero
test ! -e /opt/junkzero && test ! -e /usr/bin/junkzero
if crontab -l 2>/dev/null | grep -q 'JunkZero scheduled scan'; then echo "Uninstall left the cron schedule"; exit 1; fi
crontab -l | grep -q 'echo my own job' || { echo "Uninstall removed the user's own cron job"; exit 1; }
test ! -e "$units/junkzero-scan.timer" || { echo "Uninstall left the systemd timer"; exit 1; }
echo "The .deb installs, schedules a scan, and uninstalls cleanly"
