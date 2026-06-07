#!/bin/bash
# Web UI runner with auto-update
# Usage: ./web.sh [options]
#   --foreground, -f   Run in foreground (default: background)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
UPDATER="$SCRIPT_DIR/updater.py"
WEB="$SCRIPT_DIR/web.py"

# Check for updates (non-blocking, errors are silent)
if [ -f "$UPDATER" ]; then
    python3 "$UPDATER" 2>/dev/null || true
fi

# Run web UI (background by default)
python3 "$WEB" "$@"
