#!/usr/bin/env bash
# Container start (Railway / Docker): make the data volume writable, start the virtual display the LinkedIn
# reader's browser draws on, load the hand-made history once, then run Khushi as a normal user.
set -euo pipefail
DATA="${DATA_DIR:-/app/data}"
if [ "$(id -u)" = "0" ]; then
  mkdir -p "$DATA"
  chown -R agent:agent "$DATA"
  exec gosu agent "$0" "$@"
fi
mkdir -p "$DATA/logs" "$DATA/linkedin" "$DATA/linkedin-posting"
chmod 700 "$DATA/linkedin"

# Virtual display for the headed Chromium (same setup as the reader's own image). A stale socket from a killed
# container would make Chromium start against a display that never came up, so clear it first.
DISPLAY_NUM="${DISPLAY#:}"
DISPLAY_NUM="${DISPLAY_NUM%%.*}"
rm -f "/tmp/.X11-unix/X${DISPLAY_NUM}" "/tmp/.X${DISPLAY_NUM}-lock"
Xvfb "$DISPLAY" -screen 0 1920x1080x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
for _ in $(seq 1 100); do
  [ -S "/tmp/.X11-unix/X${DISPLAY_NUM}" ] && break
  sleep 0.1
done
[ -S "/tmp/.X11-unix/X${DISPLAY_NUM}" ] || { cat /tmp/xvfb.log >&2; exit 1; }

python -m app seed || true
exec "$@"
