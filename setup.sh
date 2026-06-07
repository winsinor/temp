#!/bin/bash
# One-time setup: download all files, install deps, and start the metrics dashboard.
# Run once on Linux/Raspberry Pi:
#   curl -sO https://raw.githubusercontent.com/winsinor/temp/main/setup.sh && bash setup.sh

set -e

BASE_URL="https://raw.githubusercontent.com/winsinor/temp/main"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DASHBOARD="$SCRIPT_DIR/dashboard.py"

echo "=== System Metrics Dashboard Setup ==="

# Download dashboard
echo "Downloading dashboard..."
curl -fsSL "$BASE_URL/dashboard.py" -o "$DASHBOARD"
chmod +x "$DASHBOARD"
echo "Downloaded dashboard.py"

# Check Python version
if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: Python 3 is required but not installed"
    exit 1
fi

echo "Python $(python3 --version) found"

# Verify dependencies (all are stdlib except nvidia-smi which is optional)
echo "Checking system dependencies..."

if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "Warning: nvidia-smi not found. GPU metrics will be unavailable."
    echo "To enable GPU monitoring, install NVIDIA drivers:"
    echo "  https://docs.nvidia.com/datacenter/tesla/tesla-installation-checklist/"
fi

echo ""
echo "=== Starting Dashboard ==="
echo "Press Ctrl+C to stop"
echo ""

python3 "$DASHBOARD"
