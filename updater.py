#!/usr/bin/env python3
"""
Auto-updater for System Metrics Dashboard
Checks for updates from GitHub and upgrades if newer version available.
"""

import os
import sys
import subprocess
import json
import hashlib
import time
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

REPO_URL = "https://raw.githubusercontent.com/winsinor/temp/main"
FILES_TO_UPDATE = ["dashboard.py", "dashboard-hyperpixel.py", "setup.sh", "web.py"]
SCRIPT_DIR = Path(__file__).parent.absolute()
VERSION_FILE = SCRIPT_DIR / ".version"

def get_file_hash(filepath):
    """Get SHA256 hash of a file."""
    try:
        with open(filepath, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except FileNotFoundError:
        return None

def get_remote_file_hash(filename):
    """Get SHA256 hash of remote file from GitHub."""
    try:
        url = f"{REPO_URL}/{filename}"
        with urlopen(url, timeout=5) as response:
            content = response.read()
            return hashlib.sha256(content).hexdigest()
    except (URLError, Exception) as e:
        print(f"Warning: Could not check hash for {filename}: {e}", file=sys.stderr)
        return None

def download_file(filename):
    """Download a file from the remote repository."""
    try:
        url = f"{REPO_URL}/{filename}"
        filepath = SCRIPT_DIR / filename
        print(f"Downloading {filename}...", file=sys.stderr)

        with urlopen(url, timeout=10) as response:
            content = response.read()

        # Write to temp file first, then move
        temp_path = filepath.with_suffix(filepath.suffix + ".tmp")
        with open(temp_path, "wb") as f:
            f.write(content)

        # Make executable if needed
        if filename.endswith(".py") or filename.endswith(".sh"):
            os.chmod(temp_path, 0o755)

        temp_path.replace(filepath)
        print(f"Updated {filename}", file=sys.stderr)
        return True
    except Exception as e:
        print(f"Error downloading {filename}: {e}", file=sys.stderr)
        return False

def check_and_update():
    """Check for updates and update if newer version available."""
    updates_available = False

    for filename in FILES_TO_UPDATE:
        local_path = SCRIPT_DIR / filename

        # Skip if file doesn't exist locally
        if not local_path.exists():
            continue

        local_hash = get_file_hash(local_path)
        remote_hash = get_remote_file_hash(filename)

        if remote_hash is None:
            continue

        if local_hash != remote_hash:
            print(f"Update available for {filename}", file=sys.stderr)
            updates_available = True
            if not download_file(filename):
                return False

    if updates_available:
        print("Dashboard updated successfully", file=sys.stderr)
        return True
    else:
        print("Dashboard is up to date", file=sys.stderr)
        return False

def main():
    """Main entry point for auto-updater."""
    if len(sys.argv) > 1:
        if sys.argv[1] == "--check":
            # Just check, don't update
            for filename in FILES_TO_UPDATE:
                local_path = SCRIPT_DIR / filename
                if local_path.exists():
                    local_hash = get_file_hash(local_path)
                    remote_hash = get_remote_file_hash(filename)
                    if remote_hash and local_hash != remote_hash:
                        print(f"Update available: {filename}")
                        return 1
            return 0
        elif sys.argv[1] == "--help":
            print("""
Auto-updater for System Metrics Dashboard

Usage:
  python3 updater.py              # Check and update if needed
  python3 updater.py --check      # Only check for updates
  python3 updater.py --help       # Show this help

The updater automatically checks file hashes against the remote repository
and downloads updates only if changes are detected.
""")
            return 0

    # Perform update check
    try:
        check_and_update()
        return 0
    except Exception as e:
        print(f"Updater error: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
