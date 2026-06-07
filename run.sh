#!/bin/bash
# Dashboard runner with auto-update
# Usage: ./run.sh [dashboard options]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
UPDATER="$SCRIPT_DIR/updater.py"
DASHBOARD="$SCRIPT_DIR/dashboard.py"

# Check for updates (non-blocking, errors are silent)
if [ -f "$UPDATER" ]; then
    python3 "$UPDATER" 2>/dev/null || true
fi

# Run dashboard
exec python3 "$DASHBOARD" "$@"
