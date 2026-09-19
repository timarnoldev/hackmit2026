#!/usr/bin/env bash
# Serve the Erbgut deck on http://localhost so the presenter window always syncs.
# Usage: ./serve.sh [port]     (default: the first free port from 8000 up)
# Needs only python3. Works offline.
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Open index.html directly instead (see README.md)." >&2
  exit 1
fi

port="${1:-}"
if [ -z "$port" ]; then
  port="$(python3 - <<'PY'
import socket
for p in range(8000, 8100):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", p))
    except OSError:
        continue
    finally:
        s.close()
    print(p)
    break
PY
)"
fi

url="http://localhost:${port}/"
echo
echo "  Erbgut deck:   ${url}"
echo "  With sample data:   ${url}?sample"
echo "  Presenter window:   press P in the deck (or open ${url}?presenter)"
echo "  Keys:               press ? in the deck"
echo
echo "  Ctrl+C to stop."
echo

exec python3 -m http.server "$port" --bind 127.0.0.1
