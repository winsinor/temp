#!/usr/bin/env python3
"""Web UI for system metrics dashboard with time range selection."""

import json
import gzip
import time
import threading
import sys
import os
from collections import deque
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import subprocess
import shutil
import re
import signal
import socket

INTERVAL = 1.0
DISK_RE = re.compile(r'^(sd[a-z]+|nvme\d+n\d+|mmcblk\d+|vd[a-z]+|xvd[a-z]+|hd[a-z]+|sr[0-9]+)$')


class MetricsCollector:
    def __init__(self):
        self.prev_idle, self.prev_total = self._read_cpu_stat()
        self.has_nvidia = shutil.which("nvidia-smi") is not None
        self.gpu_count = 0
        if self.has_nvidia:
            self._detect_gpu_count()
        self._prev_disk = {}
        self._prev_disk_t = time.monotonic()
        self._prev_net = {}
        self._prev_net_t = time.monotonic()
        self.cpu_model = self._get_cpu_model()
        self.gpu_models = self._get_gpu_models()

    def _read_cpu_stat(self):
        try:
            with open("/proc/stat") as f:
                parts = f.readline().split()
            vals = [int(x) for x in parts[1:]]
            return vals[3], sum(vals)
        except (FileNotFoundError, ValueError):
            return 0, 0

    def _get_cpu_usage(self):
        idle, total = self._read_cpu_stat()
        di, dt = idle - self.prev_idle, total - self.prev_total
        self.prev_idle, self.prev_total = idle, total
        return 100.0 * (1.0 - di / dt) if dt > 0 else 0.0

    def _get_cpu_temp(self):
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                return float(f.read()) / 1000.0
        except (FileNotFoundError, ValueError):
            pass
        # No ACPI thermal zone (common on AMD boards) - fall back to the
        # k10temp/coretemp hwmon sensor directly.
        try:
            for hw in os.listdir("/sys/class/hwmon"):
                base = f"/sys/class/hwmon/{hw}"
                try:
                    with open(f"{base}/name") as f:
                        nm = f.read().strip().lower()
                except OSError:
                    continue
                if nm in ("k10temp", "coretemp", "zenpower"):
                    for fn in sorted(os.listdir(base)):
                        if fn.startswith("temp") and fn.endswith("_input"):
                            try:
                                with open(f"{base}/{fn}") as f:
                                    return float(f.read()) / 1000.0
                            except (OSError, ValueError):
                                continue
        except OSError:
            pass
        return None

    def _detect_gpu_count(self):
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--list-gpus"],
                stderr=subprocess.DEVNULL, timeout=5, text=True,
            ).strip()
            self.gpu_count = len(out.split('\n')) if out else 0
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            self.gpu_count = 0

    def _get_cpu_model(self):
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except (FileNotFoundError, ValueError):
            pass
        return "CPU"

    def _get_gpu_models(self):
        if not self.has_nvidia or self.gpu_count == 0:
            return []
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                stderr=subprocess.DEVNULL, timeout=5, text=True,
            ).strip()
            return out.split('\n') if out else []
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            return []

    def _get_gpu_stats(self):
        if not self.has_nvidia or self.gpu_count == 0:
            return [], [], [], []
        try:
            out = subprocess.check_output(
                ["nvidia-smi",
                 "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                stderr=subprocess.DEVNULL, timeout=5, text=True,
            ).strip()
            usage, temps, mem_used_gb, mem_total_gb = [], [], [], []
            for line in out.split('\n'):
                p = [x.strip() for x in line.split(',')]
                if len(p) >= 4:
                    usage.append(float(p[0]))
                    temps.append(float(p[1]))
                    mu, mt = float(p[2]), float(p[3])
                    mem_used_gb.append(mu / 1024)   # MiB → GiB
                    mem_total_gb.append(mt / 1024)
            return usage, temps, mem_used_gb, mem_total_gb
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                ValueError, FileNotFoundError):
            return [], [], [], []

    def _get_ram(self):
        try:
            with open("/proc/meminfo") as f:
                mi = {}
                for line in f:
                    k, v = line.split(':')
                    mi[k.strip()] = int(v.split()[0])
            total_kb = mi.get("MemTotal", 1)
            avail_kb = mi.get("MemAvailable", 0)
            used_kb = total_kb - avail_kb
            cached_kb = mi.get("Cached", 0) + mi.get("Buffers", 0)
            swap_total_kb = mi.get("SwapTotal", 0)
            swap_used_kb = swap_total_kb - mi.get("SwapFree", 0)
            return {
                "pct": 100.0 * used_kb / total_kb,
                "total_gb": total_kb / 1048576,
                "used_gb": used_kb / 1048576,
                "cached_gb": cached_kb / 1048576,
                "swap_used_gb": swap_used_kb / 1048576,
                "swap_total_gb": swap_total_kb / 1048576,
            }
        except (FileNotFoundError, ValueError, KeyError):
            return {"pct": 0.0, "total_gb": 0.0, "used_gb": 0.0,
                    "cached_gb": 0.0, "swap_used_gb": 0.0, "swap_total_gb": 0.0}

    def _get_ram_temp(self):
        """DIMM temperature, if the board exposes one (jc42/spd5118 hwmon).
        Most consumer hardware has no RAM temp sensor, so this returns None."""
        try:
            for hw in os.listdir("/sys/class/hwmon"):
                base = f"/sys/class/hwmon/{hw}"
                try:
                    with open(f"{base}/name") as f:
                        nm = f.read().strip().lower()
                except OSError:
                    continue
                if nm in ("jc42", "spd5118") or "dimm" in nm:
                    for fn in sorted(os.listdir(base)):
                        if fn.startswith("temp") and fn.endswith("_input"):
                            try:
                                with open(f"{base}/{fn}") as f:
                                    return float(f.read()) / 1000.0
                            except (OSError, ValueError):
                                continue
        except OSError:
            pass
        return None

    def _dev_to_disk(self, dev):
        """Map a mount source device node (no /dev/ prefix) to its whole disk."""
        if "/" in dev:           # device-mapper / LVM / mapper — skip
            return None
        m = re.match(r"^(nvme\d+n\d+|mmcblk\d+)p\d+$", dev)
        if m:
            return m.group(1)
        m = re.match(r"^(sd[a-z]+|vd[a-z]+|xvd[a-z]+|hd[a-z])\d+$", dev)
        if m:
            return m.group(1)
        return dev if DISK_RE.match(dev) else None

    # Preferred order of /dev/disk/by-id alias prefixes — earlier = more stable
    # and serial-bearing. wwn is last because some drives report a null WWN.
    _ID_PREFIXES = ("nvme-eui.", "nvme-", "ata-", "usb-", "scsi-", "wwn-")

    def _disk_id_map(self):
        """Map kernel disk name (sdX) -> a stable per-drive identifier from
        /dev/disk/by-id. This is tied to the drive's serial/WWN, so a custom
        name keyed on it follows the physical drive across replug even when it
        comes back as a different sdX letter."""
        best = {}   # name -> (priority, id_string)
        bydir = "/dev/disk/by-id"
        try:
            entries = os.listdir(bydir)
        except OSError:
            return {}
        for entry in entries:
            if "-part" in entry:        # partition alias, not the whole disk
                continue
            try:
                dev = os.path.basename(os.path.realpath(os.path.join(bydir, entry)))
            except OSError:
                continue
            if not DISK_RE.match(dev):
                continue
            prio = next((i for i, p in enumerate(self._ID_PREFIXES)
                         if entry.startswith(p)), len(self._ID_PREFIXES))
            cur = best.get(dev)
            if cur is None or prio < cur[0]:
                best[dev] = (prio, entry)
        return {dev: eid for dev, (prio, eid) in best.items()}

    def _disk_size_bytes(self, name):
        """Physical capacity of a whole disk (512-byte sectors → bytes)."""
        try:
            with open(f"/sys/block/{name}/size") as f:
                return int(f.read()) * 512
        except (OSError, ValueError):
            return 0

    def _dm_underlying(self, dm, seen=None):
        """Resolve a device-mapper node (dm-N) to the physical disks behind it,
        walking nested mappers (LVM-on-LUKS, etc.) via /sys/block/*/slaves."""
        seen = set() if seen is None else seen
        disks = set()
        try:
            for s in os.listdir(f"/sys/block/{dm}/slaves"):
                if s in seen:
                    continue
                seen.add(s)
                if s.startswith("dm-"):
                    disks |= self._dm_underlying(s, seen)
                else:
                    d = self._dev_to_disk(s)
                    if d:
                        disks.add(d)
        except OSError:
            pass
        return disks

    def _source_disks(self, src):
        """Map a mount source (/dev/...) to the physical disk(s) it lives on,
        following /dev/mapper and LVM/dm so volumes count toward the real drive."""
        try:
            real = os.path.realpath(src)
        except OSError:
            real = src
        dev = real[5:] if real.startswith("/dev/") else real
        if dev.startswith("dm-"):
            return self._dm_underlying(dev)
        d = self._dev_to_disk(dev)
        return {d} if d else set()

    def _disk_used(self):
        """Return {disk: used_bytes} summed over mounted filesystems, attributing
        LVM/device-mapper volumes back to their underlying physical disk(s)."""
        used, seen = {}, set()
        try:
            with open("/proc/mounts") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) < 2 or not parts[0].startswith("/dev/"):
                        continue
                    src, mp = parts[0], parts[1]
                    if mp in seen:
                        continue
                    disks = self._source_disks(src)
                    if not disks:
                        continue
                    try:
                        st = os.statvfs(mp)
                    except OSError:
                        continue
                    seen.add(mp)
                    u = st.f_blocks * st.f_frsize - st.f_bfree * st.f_frsize
                    for d in disks:
                        used[d] = used.get(d, 0) + u
        except OSError:
            pass
        return used

    def _disk_meta(self, name):
        """Read model + removable flag fresh each tick so names track hot-plug."""
        model, removable = "", False
        try:
            with open(f"/sys/block/{name}/device/model") as f:
                model = f.read().strip()
        except OSError:
            pass
        try:
            with open(f"/sys/block/{name}/removable") as f:
                removable = f.read().strip() == "1"
        except OSError:
            pass
        if not model:
            model = "Removable" if removable else name
        return model, removable

    def _disk_type(self, name, removable):
        """Classify a drive — NVMe / Flash / HDD / SSD — from sysfs rather than
        its name. removable is checked before rotational because cheap USB flash
        drives often wrongly report rotational=1."""
        if re.match(r"^nvme\d+n\d+$", name):
            return "NVMe"
        if removable:
            return "Flash"
        try:
            with open(f"/sys/block/{name}/queue/rotational") as f:
                if f.read().strip() == "1":
                    return "HDD"
        except OSError:
            pass
        return "SSD"

    def _get_disks(self):
        """Per-disk activity (busy %, R/W MB/s) + capacity, like Task Manager.

        Enumerated live every tick, so hot-plugged / removable drives appear
        and disappear on their own and their names always reflect the device
        currently behind the node.
        """
        now = time.monotonic()
        dt = now - self._prev_disk_t
        disks = []
        try:
            curr = {}
            with open("/proc/diskstats") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 14 and DISK_RE.match(parts[2]):
                        # sectors read, sectors written, ms doing I/O (io_ticks)
                        curr[parts[2]] = (int(parts[5]), int(parts[9]), int(parts[12]))

            used_map = self._disk_used()
            id_map = self._disk_id_map()
            for name, (sr, sw, ticks) in sorted(curr.items()):
                r_mb = w_mb = busy = 0.0
                if name in self._prev_disk and dt > 0:
                    pr, pw, pt = self._prev_disk[name]
                    r_mb = max(0.0, (sr - pr) * 512 / (dt * 1048576))
                    w_mb = max(0.0, (sw - pw) * 512 / (dt * 1048576))
                    busy = max(0.0, min(100.0, (ticks - pt) / (dt * 1000.0) * 100.0))
                model, removable = self._disk_meta(name)
                dtype = self._disk_type(name, removable)
                # Total = physical disk capacity; used = filesystems on it (incl.
                # LVM/dm). Drives with no mounted filesystem report size only.
                total_b = self._disk_size_bytes(name)
                total_gb = total_b / 1073741824
                if name in used_map and total_b > 0:
                    used_gb = used_map[name] / 1073741824
                    used_pct = min(100.0, 100.0 * used_map[name] / total_b)
                else:
                    used_gb, used_pct = 0.0, None
                # Stable rename key (serial/WWN based), falling back to the node
                # name when no /dev/disk/by-id alias exists.
                disks.append({
                    "name": name,
                    "key": "disk:" + id_map.get(name, name),
                    "model": model,
                    "type": dtype,
                    "removable": removable,
                    "busy": busy,
                    "read": r_mb,
                    "write": w_mb,
                    "used_pct": used_pct,
                    "used_gb": used_gb,
                    "total_gb": total_gb,
                })
            self._prev_disk = curr
            self._prev_disk_t = now
        except (FileNotFoundError, ValueError, OSError):
            self._prev_disk_t = now
        return disks

    def _get_network_io(self):
        now = time.monotonic()
        dt = now - self._prev_net_t
        try:
            with open("/proc/net/dev") as f:
                curr = {}
                for line in f:
                    line = line.strip()
                    if ':' not in line:
                        continue
                    iface, data = line.split(':', 1)
                    iface = iface.strip()
                    if iface == 'lo':
                        continue
                    parts = data.split()
                    if len(parts) >= 9:
                        curr[iface] = (int(parts[0]), int(parts[8]))  # rx_bytes, tx_bytes

            rx_mb = tx_mb = 0.0
            if self._prev_net and dt > 0:
                for iface, (rx, tx) in curr.items():
                    if iface in self._prev_net:
                        prx, ptx = self._prev_net[iface]
                        rx_mb += (rx - prx) / (dt * 1048576)
                        tx_mb += (tx - ptx) / (dt * 1048576)

            self._prev_net = curr
            self._prev_net_t = now
            return max(0.0, rx_mb), max(0.0, tx_mb)
        except (FileNotFoundError, ValueError, OSError):
            self._prev_net_t = now
            return 0.0, 0.0

    def collect(self):
        gpu_usage, gpu_temp, gpu_mem_used, gpu_mem_total = self._get_gpu_stats()
        ram = self._get_ram()
        net_rx, net_tx = self._get_network_io()
        return {
            "cpu": self._get_cpu_usage(),
            "cpu_temp": self._get_cpu_temp(),
            "gpu_usage": gpu_usage,
            "gpu_temp": gpu_temp,
            "gpu_mem_used": gpu_mem_used,
            "gpu_mem_total": gpu_mem_total,
            "ram": ram["pct"],
            "ram_total_gb": ram["total_gb"],
            "ram_used_gb": ram["used_gb"],
            "ram_cached_gb": ram["cached_gb"],
            "ram_swap_used_gb": ram["swap_used_gb"],
            "ram_swap_total_gb": ram["swap_total_gb"],
            "ram_temp": self._get_ram_temp(),
            "disks": self._get_disks(),
            "net_rx": net_rx,
            "net_tx": net_tx,
        }


# Multi-resolution retention: (sample_seconds, retention_seconds), finest first.
# Data ages out of fine tiers into coarse ones, so a wide range (e.g. 24h) is
# returned as a few thousand averaged points instead of tens of thousands of
# raw 1s samples — far less to serialize, ship, and parse.
#
# These rungs are NOT tied to the UI's time-range buttons. query() dynamically
# picks the finest rung that spans whatever range is asked for, so the buttons
# can be any values without touching this ladder — they just need to fit within
# the coarsest rung's retention (73 h below).
TIERS = [
    (1,    1200),    # 1s samples,  last 20 min
    (10,   14400),   # 10s samples, last 4 h
    (60,   86400),   # 60s samples, last 24 h
    (300,  262800),  # 5min samples, last 73 h
]
# Hard ceiling on points returned for any single range. The browser re-buckets
# to ~200 points anyway, so this only needs to be comfortably above that.
MAX_POINTS = 1500


def _decimate(times, series, max_points):
    """Average down to at most max_points evenly-sized groups, keeping shape."""
    n = len(times)
    if n <= max_points:
        return times, series
    out_t = []
    out_s = {k: [] for k in series}
    for i in range(max_points):
        a = i * n // max_points
        b = (i + 1) * n // max_points
        if b <= a:
            continue
        span = b - a
        out_t.append(sum(times[a:b]) / span)
        for k, vals in series.items():
            out_s[k].append(sum(vals[a:b]) / span)
    return out_t, out_s


class TieredHistory:
    """Several named scalar series kept at multiple time resolutions.

    All series share one clock: append() is called once per collection tick
    with the same timestamp, so the per-tier deques stay index-aligned and can
    be sliced together. Each tier keeps a running mean over its current bucket
    and only commits a point when the bucket window rolls over."""

    def __init__(self, names):
        self.names = list(names)
        self._tiers = []
        for res, retain in TIERS:
            maxlen = max(1, retain // res)
            self._tiers.append({
                "res": res,
                "maxlen": maxlen,
                "ts": deque(maxlen=maxlen),
                "series": {n: deque(maxlen=maxlen) for n in self.names},
                "key": None,                         # current open bucket index
                "sum": {n: 0.0 for n in self.names},
                "cnt": 0,
            })

    def append(self, now, values):
        for t in self._tiers:
            key = int(now // t["res"])
            if t["key"] is None:
                t["key"] = key
            elif key != t["key"]:
                self._commit(t)
                t["key"] = key
            for n in self.names:
                t["sum"][n] += values.get(n, 0.0)
            t["cnt"] += 1

    def _commit(self, t):
        if t["cnt"] == 0:
            return
        t["ts"].append(t["key"] * t["res"])
        for n in self.names:
            t["series"][n].append(t["sum"][n] / t["cnt"])
            t["sum"][n] = 0.0
        t["cnt"] = 0

    def query(self, seconds, now):
        """Return (times, {name: values}) for the last `seconds`: pick the
        finest tier that spans the range, include the still-open bucket so the
        latest reading is never missing, trim to the window, then decimate."""
        tier = self._tiers[-1]
        for t in self._tiers:
            if t["res"] * t["maxlen"] >= seconds:
                tier = t
                break
        times = list(tier["ts"])
        series = {n: list(tier["series"][n]) for n in self.names}
        if tier["cnt"] > 0:
            times.append(tier["key"] * tier["res"])
            for n in self.names:
                series[n].append(tier["sum"][n] / tier["cnt"])
        cutoff = now - seconds
        lo = len(times)
        for i, ts in enumerate(times):
            if ts >= cutoff:
                lo = i
                break
        times = times[lo:]
        series = {n: v[lo:] for n, v in series.items()}
        return _decimate(times, series, MAX_POINTS)

    def to_dict(self):
        """Plain-data snapshot for persistence (deques → lists)."""
        return {
            "names": self.names,
            "tiers": [{
                "res": t["res"],
                "ts": list(t["ts"]),
                "series": {n: list(t["series"][n]) for n in self.names},
                "key": t["key"],
                "sum": dict(t["sum"]),
                "cnt": t["cnt"],
            } for t in self._tiers],
        }

    def load_dict(self, d):
        """Restore a to_dict() snapshot in place. Returns False (and changes
        nothing) if the series set no longer matches — e.g. GPU count changed."""
        if d.get("names") != self.names:
            return False
        saved = {t["res"]: t for t in d.get("tiers", [])}
        for t in self._tiers:
            s = saved.get(t["res"])
            if not s:
                continue
            t["ts"].clear()
            t["ts"].extend(s.get("ts", [])[-t["maxlen"]:])
            for n in self.names:
                dq = t["series"][n]
                dq.clear()
                dq.extend(s.get("series", {}).get(n, [])[-t["maxlen"]:])
            t["key"] = s.get("key")
            t["sum"] = {n: s.get("sum", {}).get(n, 0.0) for n in self.names}
            t["cnt"] = s.get("cnt", 0)
        return True


class MetricsStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.collector = MetricsCollector()
        self._ram_total_gb = 0.0
        self._ram_extra = {}      # latest swap/cached/temp snapshot
        self._gpu_mem_total_gb = []
        # User-assigned device names, persisted to disk so they survive restarts.
        self._names_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "device_names.json")
        self.custom_names = self._load_names()
        # Per-disk history is dynamic: keyed by device name, created on first
        # sighting and pruned once a drive has been gone for a while. Each entry
        # holds its own TieredHistory plus the latest scalar metadata.
        self._disks = {}          # name -> {"hist": TieredHistory, meta...}
        self._disk_last = {}      # name -> last-seen wall-clock time
        # GPU count is fixed at startup, so the series set is stable and the
        # tier deques stay index-aligned across all metrics.
        self._ngpu = min(self.collector.gpu_count, 4)
        names = ["cpu", "cpu_temp", "ram", "ram_used_gb", "net_rx", "net_tx"]
        for i in range(self._ngpu):
            names += [f"gpu{i}", f"gpu{i}_temp", f"gpu{i}_mem"]
        self.history = TieredHistory(names)
        self._latest = {}         # most recent raw scalars for "current" readouts
        # Rolled-up history is snapshotted here (gzipped) so up to 73 h survive a
        # restart. Written only every PERSIST_INTERVAL and on shutdown, never
        # per-tick, to keep disk writes lean.
        self._history_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "metrics_history.json.gz")
        self._load_history()

    # How long a vanished drive lingers in history before being dropped.
    DISK_PRUNE_AFTER = 600
    # How often the in-memory tiers are flushed to disk.
    PERSIST_INTERVAL = 300

    def _save_history(self):
        """Snapshot all tiers to disk atomically. Builds the snapshot under the
        lock, then writes outside it so collection isn't blocked on disk I/O."""
        with self.lock:
            snapshot = {
                "version": 1,
                "saved_at": time.time(),
                "main": self.history.to_dict(),
                "disks": {
                    name: {
                        "hist": h["hist"].to_dict(),
                        "meta": {k: h[k] for k in
                                 ("key", "model", "type", "removable",
                                  "used_pct", "used_gb", "total_gb") if k in h},
                        "last": self._disk_last.get(name, 0),
                    } for name, h in self._disks.items()
                },
                "ram_total_gb": self._ram_total_gb,
                "gpu_mem_total_gb": self._gpu_mem_total_gb,
                "ram_extra": self._ram_extra,
                "latest": self._latest,
            }
        try:
            tmp = self._history_file + ".tmp"
            with gzip.open(tmp, "wt", encoding="utf-8") as f:
                json.dump(snapshot, f)
            os.replace(tmp, self._history_file)
        except OSError as e:
            print(f"Could not persist history: {e}")

    def _load_history(self):
        """Restore a previous snapshot, if any. Tolerates a missing/corrupt file
        or changed hardware by falling back to an empty history."""
        if not os.path.exists(self._history_file):
            return
        try:
            with gzip.open(self._history_file, "rt", encoding="utf-8") as f:
                snap = json.load(f)
        except (OSError, ValueError, EOFError) as e:
            print(f"Could not load history: {e}")
            return
        self.history.load_dict(snap.get("main", {}))
        for name, dd in snap.get("disks", {}).items():
            h = {"hist": TieredHistory(["busy", "read", "write"])}
            h["hist"].load_dict(dd.get("hist", {}))
            h.update(dd.get("meta", {}))
            self._disks[name] = h
            self._disk_last[name] = dd.get("last", 0)
        self._ram_total_gb = snap.get("ram_total_gb", self._ram_total_gb)
        self._gpu_mem_total_gb = snap.get("gpu_mem_total_gb", self._gpu_mem_total_gb)
        self._ram_extra = snap.get("ram_extra", self._ram_extra)
        self._latest = snap.get("latest", self._latest)

    def _store(self, metrics):
        """Append one collected sample to history. Caller need not hold the lock."""
        now = time.time()
        with self.lock:
            vals = {
                "cpu": metrics["cpu"],
                "cpu_temp": metrics["cpu_temp"] or 0,
                "ram": metrics["ram"],
                "ram_used_gb": metrics["ram_used_gb"],
                "net_rx": metrics["net_rx"],
                "net_tx": metrics["net_tx"],
            }
            if metrics["ram_total_gb"] > 0:
                self._ram_total_gb = metrics["ram_total_gb"]
            self._ram_extra = {
                "cached_gb": metrics["ram_cached_gb"],
                "swap_used_gb": metrics["ram_swap_used_gb"],
                "swap_total_gb": metrics["ram_swap_total_gb"],
                "temp": metrics["ram_temp"],
            }
            gpu_temps = []
            for i in range(self._ngpu):
                u = metrics["gpu_usage"][i] if i < len(metrics["gpu_usage"]) else 0
                t = metrics["gpu_temp"][i] if i < len(metrics["gpu_temp"]) else 0
                m = metrics["gpu_mem_used"][i] if i < len(metrics["gpu_mem_used"]) else 0
                vals[f"gpu{i}"] = u
                vals[f"gpu{i}_temp"] = t
                vals[f"gpu{i}_mem"] = m
                gpu_temps.append(t)
            if metrics["gpu_mem_total"]:
                self._gpu_mem_total_gb = metrics["gpu_mem_total"]

            self.history.append(now, vals)
            self._latest = {"cpu_temp": metrics["cpu_temp"] or 0,
                            "gpu_temps": gpu_temps}

            for d in metrics["disks"]:
                name = d["name"]
                h = self._disks.get(name)
                if h is None:
                    h = self._disks[name] = {
                        "hist": TieredHistory(["busy", "read", "write"]),
                    }
                h["hist"].append(now, {"busy": d["busy"],
                                       "read": d["read"],
                                       "write": d["write"]})
                h["key"] = d["key"]
                h["model"] = d["model"]
                h["type"] = d["type"]
                h["removable"] = d["removable"]
                h["used_pct"] = d["used_pct"]
                h["used_gb"] = d["used_gb"]
                h["total_gb"] = d["total_gb"]
                self._disk_last[name] = now

            # Drop drives that have been unplugged long enough.
            for name in [n for n, t in self._disk_last.items()
                         if now - t > self.DISK_PRUNE_AFTER]:
                self._disks.pop(name, None)
                self._disk_last.pop(name, None)

    def rescan(self):
        """Force an immediate collection so freshly mounted/unmounted drives
        (and renamed devices) show up right away instead of on the next tick.
        Triggered by the web UI's Refresh button."""
        try:
            self._store(self.collector.collect())
        except Exception as e:
            print(f"Error during rescan: {e}")

    def _load_names(self):
        try:
            with open(self._names_file) as f:
                d = json.load(f)
            return {str(k): str(v) for k, v in d.items() if v}
        except (OSError, ValueError, AttributeError):
            return {}

    def set_name(self, key, name):
        """Set (or, with an empty name, clear) a user-assigned device title."""
        with self.lock:
            if name:
                self.custom_names[key] = name
            else:
                self.custom_names.pop(key, None)
            try:
                tmp = self._names_file + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(self.custom_names, f)
                os.replace(tmp, self._names_file)
            except OSError as e:
                print(f"Could not persist device names: {e}")

    def collect_loop(self):
        last_save = time.time()
        while True:
            try:
                self._store(self.collector.collect())
                if time.time() - last_save >= self.PERSIST_INTERVAL:
                    self._save_history()
                    last_save = time.time()
                time.sleep(INTERVAL)
            except Exception as e:
                print(f"Error in collection loop: {e}")
                time.sleep(INTERVAL)

    def get_data(self, seconds=300):
        """Get metrics data for the last N seconds, at a resolution that
        coarsens with the requested range so payloads stay small."""
        now = time.time()
        with self.lock:
            timestamps, main = self.history.query(int(seconds), now)
            if not timestamps:
                return None

            ngpu = self._ngpu
            gpu_data = [{
                "usage": main.get(f"gpu{i}", []),
                "temp": main.get(f"gpu{i}_temp", []),
                "mem_used_gb": main.get(f"gpu{i}_mem", []),
                "mem_total_gb": (self._gpu_mem_total_gb[i]
                                 if i < len(self._gpu_mem_total_gb) else 8.0),
            } for i in range(ngpu)]

            current_cpu_temp = self._latest.get("cpu_temp", 0)
            latest_gpu_temps = self._latest.get("gpu_temps", [])
            current_gpu_temps = [latest_gpu_temps[i] if i < len(latest_gpu_temps) else 0
                                 for i in range(ngpu)]

            # Per-disk data — only drives seen in the most recent cycle, so an
            # unplugged drive disappears from the UI on the next refresh.
            disk_data = []
            for name in sorted(self._disks):
                h = self._disks[name]
                if now - self._disk_last.get(name, 0) > 2 * INTERVAL:
                    continue
                dts, ds = h["hist"].query(int(seconds), now)
                disk_data.append({
                    "name": name,
                    "key": h.get("key", "disk:" + name),
                    "model": h.get("model", name),
                    "type": h.get("type", ""),
                    "removable": h.get("removable", False),
                    "used_pct": h.get("used_pct"),
                    "used_gb": h.get("used_gb", 0.0),
                    "total_gb": h.get("total_gb", 0.0),
                    "times": dts,
                    "busy": ds["busy"],
                    "read": ds["read"],
                    "write": ds["write"],
                })

            return {
                "times": timestamps,
                "cpu": main["cpu"],
                "cpu_temp": main["cpu_temp"],
                "cpu_model": self.collector.cpu_model,
                "cpu_current_temp": current_cpu_temp,
                "ram": main["ram"],
                "ram_used_gb": main["ram_used_gb"],
                "ram_total_gb": self._ram_total_gb,
                "ram_cached_gb": self._ram_extra.get("cached_gb", 0.0),
                "ram_swap_used_gb": self._ram_extra.get("swap_used_gb", 0.0),
                "ram_swap_total_gb": self._ram_extra.get("swap_total_gb", 0.0),
                "ram_current_temp": self._ram_extra.get("temp") or 0,
                "gpu": gpu_data,
                "gpu_models": self.collector.gpu_models[:ngpu],
                "gpu_current_temps": current_gpu_temps,
                "disks": disk_data,
                "net_rx": main["net_rx"],
                "net_tx": main["net_tx"],
                "gpu_count": ngpu,
                "custom_names": dict(self.custom_names),
            }


_HOSTNAME = socket.gethostname()

_PEER_MAP = {
    'homelab':  ('homelab2', 'http://192.168.86.54:5000'),
    'homelab2': ('homelab',  'http://192.168.86.49:5000'),
}
_PEER_NAME, _PEER_URL = _PEER_MAP.get(_HOSTNAME, (None, None))

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
    <title>__HOSTNAME__ — Metrics</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #0f0f0f;
            color: #e0e0e0;
            padding: 20px;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        h1 {
            font-size: 24px;
            margin-bottom: 20px;
            color: #fff;
        }
        .controls {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 20px;
        }
        .time-slider-bar {
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            z-index: 20;
            background: #141414;
            border-top: 1px solid #2a2a2a;
            box-shadow: 0 -4px 12px rgba(0,0,0,0.5);
            padding: 10px 24px calc(14px + env(safe-area-inset-bottom));
            display: flex;
            align-items: center;
            gap: 14px;
        }
        .time-slider-label {
            color: #e87722;
            font-size: 13px;
            font-weight: 600;
            min-width: 52px;
            text-align: right;
        }
        .time-slider-range {
            color: #606060;
            font-size: 11px;
            white-space: nowrap;
        }
        input[type=range] {
            flex: 1;
            accent-color: #e87722;
            height: 4px;
            cursor: pointer;
        }
        body { padding-bottom: 56px; }
        a.peer-link {
            margin-right: auto;
            padding: 7px 14px;
            background: #1a2a3a;
            border: 1px solid #2e5070;
            color: #6ab0dc;
            border-radius: 4px;
            font-size: 13px;
            font-weight: 600;
            text-decoration: none;
            white-space: nowrap;
        }
        a.peer-link:hover { background: #223344; border-color: #4080a0; }
        button {
            padding: 8px 16px;
            background: #2a2a2a;
            border: 1px solid #404040;
            color: #e0e0e0;
            cursor: pointer;
            border-radius: 4px;
            font-size: 14px;
            transition: all 0.2s;
        }
        button:hover {
            background: #3a3a3a;
            border-color: #505050;
        }
        button.active {
            background: #e87722;
            border-color: #e87722;
            color: #fff;
        }
        button.refresh-btn {
            margin-right: auto;
            background: #1f3a2a;
            border-color: #2e6b46;
            color: #5fdc8d;
            font-weight: 600;
        }
        button.refresh-btn:hover {
            background: #265036;
            border-color: #3a8a59;
        }
        .charts {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(min(500px, 100%), 1fr));
            gap: 20px;
        }
        .chart-container {
            background: #1a1a1a;
            border: 1px solid #2a2a2a;
            border-radius: 8px;
            padding: 15px;
            position: relative;
            min-height: 350px;
        }
        .chart-header {
            display: flex;
            justify-content: space-between;
            align-items: baseline;
            gap: 10px;
            margin-bottom: 10px;
        }
        .chart-title {
            font-size: 14px;
            font-weight: 600;
            color: #b0b0b0;
            text-transform: uppercase;
            letter-spacing: 1px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            min-width: 0;
        }
        .chart-titlewrap {
            display: flex;
            align-items: baseline;
            gap: 8px;
            min-width: 0;
        }
        .chart-id {
            font-size: 14px;
            font-weight: 600;
            color: #6e6e6e;
            text-transform: uppercase;
            letter-spacing: 1px;
            flex-shrink: 0;
        }
        .chart-title.editable { cursor: text; }
        .chart-title.editable:hover { color: #e0e0e0; }
        /* Placeholder hint for an unnamed (empty) editable title. */
        .chart-title.editable:empty::before {
            content: attr(data-placeholder);
            color: #5a5a5a;
            font-style: italic;
            text-transform: none;
            letter-spacing: normal;
        }
        .chart-title.editing:empty::before { content: ''; }
        .chart-title.editing {
            overflow: visible;
            white-space: normal;
            outline: 1px solid #e87722;
            border-radius: 3px;
            padding: 1px 4px;
            color: #fff;
            background: #232323;
            cursor: text;
            /* >=16px stops iOS/mobile Safari auto-zooming on focus */
            font-size: 16px;
        }
        .title-reset {
            display: none;
            padding: 2px 8px;
            font-size: 12px;
            background: #2a2a2a;
            border: 1px solid #404040;
            color: #b0b0b0;
            border-radius: 4px;
            cursor: pointer;
            flex-shrink: 0;
        }
        .title-reset:hover { background: #3a3a3a; color: #fff; }
        .chart-temp {
            font-size: 14px;
            font-weight: 600;
            color: #ff6b35;
            white-space: nowrap;
            flex-shrink: 0;
        }
        .chart-sub {
            font-size: 12px;
            color: #8a8a8a;
            margin: -4px 0 8px;
            letter-spacing: 0.5px;
        }
        canvas { max-height: 300px; }
        .status {
            text-align: center;
            color: #707070;
            margin-top: 10px;
            font-size: 12px;
            line-height: 1.6;
            word-break: break-word;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="controls">
            <h1>&#9670; __HOSTNAME__</h1>
            __PEER_LINK__
            <button class="refresh-btn" id="refreshBtn" title="Reload and re-scan mounted drives">&#8635; Refresh</button>
        </div>
        <div class="charts">
            <div class="chart-container">
                <div class="chart-header">
                    <div class="chart-titlewrap">
                        <span class="chart-id">CPU</span>
                        <span class="chart-title editable" id="cpuTitle"></span>
                    </div>
                    <button class="title-reset" id="cpuReset" title="Reset to default name">&#8635; Default</button>
                    <div class="chart-temp" id="cpuTemp"></div>
                </div>
                <canvas id="cpuChart"></canvas>
                <div class="status" id="cpuStatus"></div>
            </div>
            <div class="chart-container">
                <div class="chart-header">
                    <div class="chart-titlewrap">
                        <span class="chart-id">RAM</span>
                        <span class="chart-title editable" id="ramTitle"></span>
                    </div>
                    <button class="title-reset" id="ramReset" title="Reset to default name">&#8635; Default</button>
                    <div class="chart-temp" id="ramTemp"></div>
                </div>
                <div class="chart-sub" id="ramSub"></div>
                <canvas id="ramChart"></canvas>
                <div class="status" id="ramStatus"></div>
            </div>
            <div id="gpuCharts" style="display:contents"></div>
            <div id="diskCharts" style="display:contents"></div>
            <div class="chart-container">
                <div class="chart-header">
                    <div class="chart-title" id="netTitle">Network I/O</div>
                    <button class="title-reset" id="netReset" title="Reset to default name">&#8635; Default</button>
                    <div class="chart-temp" id="netTemp"></div>
                </div>
                <canvas id="netChart"></canvas>
                <div class="status" id="netStatus"></div>
            </div>
        </div>
    </div>
    <div class="time-slider-bar">
        <span class="time-slider-range">15s</span>
        <input type="range" id="timeSlider" min="0" max="8" step="1" value="2">
        <span class="time-slider-range">72h</span>
        <span class="time-slider-label" id="sliderLabel">5m</span>
    </div>

    <script>
        const charts = {};
        let currentRange = 300;
        const NICE_INTERVALS = [1, 2, 5, 10, 15, 20, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200];

        // ── Editable device titles ────────────────────────────────────────────
        // Each device has a stable key (cpu, ram, net, gpu0..., disk:sdX). Custom
        // names are kept server-side; click a title to rename, with a Default
        // button to clear the override.
        let customNames = {};
        let editingKey = null;

        function displayName(key, def) {
            return (customNames[key] && customNames[key].length) ? customNames[key] : def;
        }

        async function postName(key, name) {
            try {
                await fetch('/api/rename', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ key, name })
                });
            } catch (e) { /* keep local change even if save fails */ }
        }

        // Set a title's text from custom-or-default, unless it's being edited.
        // When the resolved name is empty, the element shows `placeholder` as a
        // greyed, clickable hint (so even unnamed devices have an edit target).
        function applyTitle(el, key, def, placeholder) {
            el.dataset.key = key;
            el.dataset.def = def;
            el.dataset.placeholder = placeholder || 'Set name…';
            if (editingKey === key) return;
            el.textContent = displayName(key, def);
        }

        function endEdit(titleEl) {
            const resetEl = document.getElementById(titleEl.dataset.resetId);
            titleEl.contentEditable = 'false';
            titleEl.classList.remove('editing');
            if (resetEl) resetEl.style.display = 'none';
            editingKey = null;
        }

        function commitEdit(titleEl) {
            if (editingKey !== titleEl.dataset.key) return;
            const key = titleEl.dataset.key;
            const def = titleEl.dataset.def || '';
            const val = titleEl.textContent.replace(/\\s+/g, ' ').trim();
            if (!val || val === def) {
                delete customNames[key];
                postName(key, '');
                titleEl.textContent = def;
            } else {
                customNames[key] = val;
                postName(key, val);
            }
            endEdit(titleEl);
        }

        function resetTitle(titleEl) {
            const key = titleEl.dataset.key;
            delete customNames[key];
            postName(key, '');
            titleEl.textContent = titleEl.dataset.def || '';
            endEdit(titleEl);
        }

        // Wire a title + its Default button for editing (idempotent).
        function ensureEditable(titleEl, resetEl) {
            if (!titleEl || titleEl.dataset.wired) return;
            titleEl.dataset.wired = '1';
            titleEl.dataset.resetId = resetEl.id;
            titleEl.classList.add('editable');
            titleEl.title = 'Click to rename';

            titleEl.addEventListener('click', () => {
                if (titleEl.isContentEditable) return;
                editingKey = titleEl.dataset.key;
                titleEl.contentEditable = 'true';
                titleEl.classList.add('editing');
                resetEl.style.display = '';
                titleEl.focus();
                const r = document.createRange();
                r.selectNodeContents(titleEl);
                const sel = getSelection();
                sel.removeAllRanges();
                sel.addRange(r);
            });
            titleEl.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') { e.preventDefault(); commitEdit(titleEl); }
                else if (e.key === 'Escape') {
                    e.preventDefault();
                    titleEl.textContent = displayName(titleEl.dataset.key, titleEl.dataset.def);
                    endEdit(titleEl);
                }
            });
            titleEl.addEventListener('blur', () => commitEdit(titleEl));
            // mousedown would blur the title first; prevent it so the click lands.
            resetEl.addEventListener('mousedown', (e) => e.preventDefault());
            resetEl.addEventListener('click', () => resetTitle(titleEl));
        }

        // Format an elapsed-seconds value for an x-axis tick.
        // x=0 = newest (right), x=seconds = oldest (left) after axis reversal.
        function fmtTime(t) {
            t = Math.round(t);
            if (t === 0) return 'now';
            if (t < 60) return t + 's';
            const min = Math.floor(t / 60);
            const sec = t % 60;
            if (t < 3600) return sec === 0 ? min + 'm' : min + 'm' + sec + 's';
            const hr = Math.floor(t / 3600);
            const rem = min % 60;
            return rem === 0 ? hr + 'h' : hr + 'h' + rem + 'm';
        }

        // Pick a clean tick spacing that yields roughly `target` labels across the range.
        function tickInterval(seconds, target) {
            const raw = seconds / target;
            return NICE_INTERVALS.find(c => c >= raw) || NICE_INTERVALS[NICE_INTERVALS.length - 1];
        }

        // Bucket samples by ABSOLUTE time. x values represent seconds-ago (0=now, seconds=oldest).
        // The axis is reversed so x=0 is on the right (newest) and x=seconds on the left (oldest).
        function bucketPoints(absTimes, arr, bucketSize, windowStart, totalDuration) {
            const sums = {}, counts = {};
            for (let i = 0; i < arr.length; i++) {
                const v = arr[i];
                if (v === null || v === undefined || isNaN(v)) continue;
                const t = absTimes[i];
                if (t < windowStart) continue;
                const b = Math.floor(t / bucketSize);
                sums[b] = (sums[b] || 0) + v;
                counts[b] = (counts[b] || 0) + 1;
            }
            const now = windowStart + totalDuration;
            const pts = [];
            for (const b in counts) {
                const center = (Number(b) + 0.5) * bucketSize;
                // x = seconds ago from now (0 = newest, totalDuration = oldest)
                const x = Math.max(0, Math.min(totalDuration, now - center));
                pts.push({ x, y: sums[b] / counts[b] });
            }
            pts.sort((a, c) => a.x - c.x);
            return pts;
        }

        function avg(arr) {
            const vals = arr.filter(v => v !== null && v !== undefined && !isNaN(v));
            return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0;
        }

        // x-axis scale config shared by all charts. Reversed: x=0 on right (now), x=seconds on left.
        function xScale(seconds, interval) {
            return {
                type: 'linear',
                min: 0,
                max: seconds,
                reverse: true,
                grid: { color: '#2a2a2a' },
                ticks: {
                    color: '#707070',
                    stepSize: interval,
                    autoSkip: false,
                    maxRotation: 0,
                    minRotation: 0,
                    callback: v => fmtTime(v)
                }
            };
        }

        function makeOptions(seconds, interval, yUnit, opts = {}) {
            const yMax = opts.yMax === undefined ? 100 : opts.yMax;
            const decimals = opts.decimals !== undefined ? opts.decimals : null;
            const tickCb = decimals !== null ? v => v.toFixed(decimals) + yUnit : v => v + yUnit;
            const y = { beginAtZero: true, grid: { color: '#2a2a2a' },
                        ticks: { color: '#707070', callback: tickCb } };
            if (yMax !== null) y.max = yMax;
            return {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                plugins: { legend: opts.legend || { display: false } },
                scales: { y, x: xScale(seconds, interval) }
            };
        }

        // GPU chart: dual y-axes — left=% usage (orange), right=VRAM GB (teal).
        function makeGpuOptions(seconds, interval, vramMaxGb) {
            return {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                plugins: { legend: { display: true, labels: { color: '#b0b0b0', boxWidth: 12 } } },
                scales: {
                    y: {
                        type: 'linear',
                        position: 'left',
                        min: 0,
                        max: 100,
                        grid: { color: '#2a2a2a' },
                        ticks: { color: '#ff9500', callback: v => v + '%' }
                    },
                    y1: {
                        type: 'linear',
                        position: 'right',
                        min: 0,
                        max: vramMaxGb,
                        grid: { drawOnChartArea: false },
                        ticks: { color: '#4ec9b0', callback: v => v.toFixed(1) + ' GB' },
                        title: { display: true, text: 'VRAM', color: '#4ec9b0', font: { size: 11 } }
                    },
                    x: xScale(seconds, interval)
                }
            };
        }

        // Disk chart: left y-axis = Active time % (0-100, cyan), right y-axis = R/W MB/s (auto).
        function makeDiskOptions(seconds, interval) {
            return {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                plugins: { legend: { display: true, labels: { color: '#b0b0b0', boxWidth: 12 } } },
                scales: {
                    y: {
                        type: 'linear',
                        position: 'left',
                        min: 0,
                        max: 100,
                        grid: { color: '#2a2a2a' },
                        ticks: { color: '#00d2ff', callback: v => v + '%' }
                    },
                    y1: {
                        type: 'linear',
                        position: 'right',
                        beginAtZero: true,
                        grid: { drawOnChartArea: false },
                        ticks: { color: '#9aa0a6', callback: v => v.toFixed(1) },
                        title: { display: true, text: 'MB/s', color: '#9aa0a6', font: { size: 11 } }
                    },
                    x: xScale(seconds, interval)
                }
            };
        }

        function lineDataset(label, color, fillColor, points) {
            return { label, data: points, borderColor: color, backgroundColor: fillColor,
                     tension: 0.4, fill: true, pointRadius: 0, borderWidth: 2 };
        }

        async function loadData(seconds, isRangeChange = false) {
            currentRange = seconds;
            try {
                const response = await fetch(`/api/metrics?seconds=${seconds}`);
                const data = await response.json();
                if (!data || data.error) return;

                // Sync custom names from server (skip while the user is editing).
                if (!editingKey) customNames = data.custom_names || {};

                const absTimes = data.times;
                const n = Math.min(200, seconds);
                const bucketSize = seconds / n;
                const now = absTimes.length ? absTimes[absTimes.length - 1] : 0;
                const windowStart = now - seconds;
                const interval = tickInterval(seconds, 9);

                const cpuPts   = bucketPoints(absTimes, data.cpu,        bucketSize, windowStart, seconds);
                const ramPts   = bucketPoints(absTimes, data.ram_used_gb, bucketSize, windowStart, seconds);
                const netRxPts = bucketPoints(absTimes, data.net_rx,      bucketSize, windowStart, seconds);
                const netTxPts = bucketPoints(absTimes, data.net_tx,      bucketSize, windowStart, seconds);

                // CPU chart
                if (!charts.cpu || isRangeChange) {
                    if (charts.cpu) charts.cpu.destroy();
                    charts.cpu = new Chart(document.getElementById('cpuChart'), {
                        type: 'line',
                        data: { datasets: [lineDataset('CPU', '#e87722', 'rgba(232, 119, 34, 0.1)', cpuPts)] },
                        options: makeOptions(seconds, interval, '%')
                    });
                } else {
                    charts.cpu.data.datasets[0].data = cpuPts;
                    charts.cpu.update('none');
                }
                const cpuAvg = avg(data.cpu).toFixed(1);
                applyTitle(document.getElementById('cpuTitle'), 'cpu', data.cpu_model || '');
                document.getElementById('cpuTemp').textContent =
                    data.cpu_current_temp > 0 ? `${data.cpu_current_temp.toFixed(0)}°C` : '';
                document.getElementById('cpuStatus').textContent = `Avg: ${cpuAvg}%`;

                // RAM chart (in GB)
                const ramTotalGb = data.ram_total_gb || 64;
                if (!charts.ram || isRangeChange) {
                    if (charts.ram) charts.ram.destroy();
                    charts.ram = new Chart(document.getElementById('ramChart'), {
                        type: 'line',
                        data: { datasets: [lineDataset('RAM', '#8c50f5', 'rgba(140, 80, 245, 0.1)', ramPts)] },
                        options: makeOptions(seconds, interval, ' GB', { yMax: ramTotalGb, decimals: 1 })
                    });
                } else {
                    charts.ram.data.datasets[0].data = ramPts;
                    charts.ram.update('none');
                }
                const ramCurrent = data.ram_used_gb.length ? data.ram_used_gb[data.ram_used_gb.length - 1].toFixed(1) : '0.0';
                applyTitle(document.getElementById('ramTitle'), 'ram', '', 'System Memory');
                // RAM temperature is only available if the board exposes a DIMM
                // sensor (jc42/spd5118); most hardware reports none.
                document.getElementById('ramTemp').textContent =
                    data.ram_current_temp > 0 ? `${data.ram_current_temp.toFixed(0)}°C` : '';
                const swapStr = data.ram_swap_total_gb > 0
                    ? ` • Swap: ${data.ram_swap_used_gb.toFixed(1)}/${data.ram_swap_total_gb.toFixed(0)} GB`
                    : '';
                document.getElementById('ramSub').textContent =
                    `Cached: ${(data.ram_cached_gb || 0).toFixed(1)} GB${swapStr}`;
                document.getElementById('ramStatus').textContent =
                    `${ramCurrent} GB used / ${ramTotalGb.toFixed(0)} GB total`;

                // GPU charts
                if (isRangeChange) {
                    document.getElementById('gpuCharts').innerHTML = '';
                    document.getElementById('diskCharts').innerHTML = '';
                    Object.keys(charts).forEach(k => {
                        if (k.startsWith('gpu') || k.startsWith('disk:')) {
                            charts[k].destroy(); delete charts[k];
                        }
                    });
                    if (charts.net) { charts.net.destroy(); delete charts.net; }
                }

                if (data.gpu_count > 0) {
                    const gpuContainer = document.getElementById('gpuCharts');
                    for (let i = 0; i < data.gpu_count; i++) {
                        const gpuPts  = bucketPoints(absTimes, data.gpu[i].usage,      bucketSize, windowStart, seconds);
                        const vramPts = bucketPoints(absTimes, data.gpu[i].mem_used_gb, bucketSize, windowStart, seconds);
                        const vramMax = data.gpu[i].mem_total_gb || 8;

                        if (!document.getElementById(`gpuChart${i}`)) {
                            const container = document.createElement('div');
                            container.className = 'chart-container';
                            container.innerHTML = `
                                <div class="chart-header">
                                    <div class="chart-titlewrap">
                                        <span class="chart-id">GPU ${i}</span>
                                        <span class="chart-title editable" id="gpuTitle${i}"></span>
                                    </div>
                                    <button class="title-reset" id="gpuReset${i}" title="Reset to default name">&#8635; Default</button>
                                    <div class="chart-temp" id="gpuTemp${i}"></div>
                                </div>
                                <canvas id="gpuChart${i}"></canvas>
                                <div class="status" id="gpuStatus${i}"></div>
                            `;
                            gpuContainer.appendChild(container);
                            ensureEditable(document.getElementById(`gpuTitle${i}`),
                                           document.getElementById(`gpuReset${i}`));
                        }
                        if (!charts[`gpu${i}`]) {
                            charts[`gpu${i}`] = new Chart(document.getElementById(`gpuChart${i}`), {
                                type: 'line',
                                data: {
                                    datasets: [
                                        Object.assign(lineDataset('Usage', '#ff9500', 'rgba(255,149,0,0.1)', gpuPts), { yAxisID: 'y' }),
                                        Object.assign(lineDataset('VRAM', '#4ec9b0', 'rgba(78,201,176,0.05)', vramPts), { yAxisID: 'y1' })
                                    ]
                                },
                                options: makeGpuOptions(seconds, interval, vramMax)
                            });
                        } else {
                            charts[`gpu${i}`].data.datasets[0].data = gpuPts;
                            charts[`gpu${i}`].data.datasets[1].data = vramPts;
                            charts[`gpu${i}`].update('none');
                        }
                        const gpuAvg  = avg(data.gpu[i].usage).toFixed(1);
                        const vramCur = data.gpu[i].mem_used_gb.length
                            ? data.gpu[i].mem_used_gb[data.gpu[i].mem_used_gb.length - 1].toFixed(2) : '0.00';
                        const gpuModel  = data.gpu_models[i] || `GPU ${i}`;
                        applyTitle(document.getElementById(`gpuTitle${i}`), 'gpu' + i, gpuModel);
                        document.getElementById(`gpuTemp${i}`).textContent =
                            data.gpu_current_temps[i] > 0 ? `${data.gpu_current_temps[i].toFixed(0)}°C` : '';
                        document.getElementById(`gpuStatus${i}`).textContent =
                            `Avg: ${gpuAvg}% • VRAM: ${vramCur} GB`;
                    }
                }

                // Per-disk charts (Task Manager style): one card per physical
                // drive, "Active time" % as the main graph, capacity in the
                // corner, dynamically added/removed as drives come and go.
                const diskContainer = document.getElementById('diskCharts');
                const disks = data.disks || [];
                const present = new Set(disks.map(d => 'disk:' + d.name));
                Object.keys(charts).forEach(k => {
                    if (k.startsWith('disk:') && !present.has(k)) {
                        charts[k].destroy(); delete charts[k];
                        const el = document.getElementById('diskCard_' + k.slice(5));
                        if (el) el.remove();
                    }
                });

                for (const d of disks) {
                    const key = 'disk:' + d.name;
                    const busyPts  = bucketPoints(d.times, d.busy,  bucketSize, windowStart, seconds);
                    const readPts  = bucketPoints(d.times, d.read,  bucketSize, windowStart, seconds);
                    const writePts = bucketPoints(d.times, d.write, bucketSize, windowStart, seconds);

                    if (!document.getElementById('diskCard_' + d.name)) {
                        const container = document.createElement('div');
                        container.className = 'chart-container';
                        container.id = 'diskCard_' + d.name;
                        container.innerHTML = `
                            <div class="chart-header">
                                <div class="chart-titlewrap">
                                    <span class="chart-id" id="diskId_${d.name}"></span>
                                    <span class="chart-title editable" id="diskTitle_${d.name}"></span>
                                </div>
                                <button class="title-reset" id="diskReset_${d.name}" title="Reset to default name">&#8635; Default</button>
                            </div>
                            <div class="chart-sub" id="diskCap_${d.name}"></div>
                            <canvas id="diskChart_${d.name}"></canvas>
                            <div class="status" id="diskStatus_${d.name}"></div>
                        `;
                        diskContainer.appendChild(container);
                        ensureEditable(document.getElementById('diskTitle_' + d.name),
                                       document.getElementById('diskReset_' + d.name));
                    }
                    if (!charts[key]) {
                        charts[key] = new Chart(document.getElementById('diskChart_' + d.name), {
                            type: 'line',
                            data: {
                                datasets: [
                                    Object.assign(lineDataset('Active %', '#00d2ff', 'rgba(0,210,255,0.12)', busyPts), { yAxisID: 'y' }),
                                    Object.assign(lineDataset('Read MB/s',  '#5fdc8d', 'rgba(95,220,141,0.05)', readPts),  { yAxisID: 'y1' }),
                                    Object.assign(lineDataset('Write MB/s', '#ff5f6d', 'rgba(255,95,109,0.05)', writePts), { yAxisID: 'y1' })
                                ]
                            },
                            options: makeDiskOptions(seconds, interval)
                        });
                    } else {
                        charts[key].data.datasets[0].data = busyPts;
                        charts[key].data.datasets[1].data = readPts;
                        charts[key].data.datasets[2].data = writePts;
                        charts[key].update('none');
                    }

                    // "sdX" (+ eject marker) is the permanent, uneditable identifier;
                    // the name beside it defaults to the model and is editable.
                    const eject = d.removable ? ' ⏏' : '';
                    document.getElementById('diskId_' + d.name).textContent = `${d.name}${eject}`;
                    const defName = (d.model && d.model !== d.name) ? d.model : '';
                    // Rename uses the stable per-drive key so the name survives
                    // replug under a different sdX letter.
                    applyTitle(document.getElementById('diskTitle_' + d.name),
                               d.key, defName);
                    // Type (SSD/HDD/NVMe/Flash) + capacity / full % below the title.
                    const dtype = d.type ? `${d.type} · ` : '';
                    document.getElementById('diskCap_' + d.name).textContent =
                        d.used_pct !== null && d.used_pct !== undefined
                            ? `${dtype}${d.used_pct.toFixed(0)}% full · ${d.used_gb.toFixed(0)}/${d.total_gb.toFixed(0)} GB`
                            : `${dtype}${d.total_gb.toFixed(0)} GB · not mounted`;
                    const busyAvg = avg(d.busy).toFixed(1);
                    document.getElementById('diskStatus_' + d.name).textContent =
                        `Active avg: ${busyAvg}% • R: ${avg(d.read).toFixed(2)} MB/s • W: ${avg(d.write).toFixed(2)} MB/s`;
                }

                // Network I/O chart
                if (!charts.net || isRangeChange) {
                    if (charts.net) charts.net.destroy();
                    charts.net = new Chart(document.getElementById('netChart'), {
                        type: 'line',
                        data: {
                            datasets: [
                                lineDataset('RX MB/s', '#00e676', 'rgba(0,230,118,0.1)', netRxPts),
                                lineDataset('TX MB/s', '#e040fb', 'rgba(224,64,251,0.1)', netTxPts)
                            ]
                        },
                        options: makeOptions(seconds, interval, ' MB/s',
                            { yMax: null, legend: { display: true, labels: { color: '#b0b0b0' } } })
                    });
                } else {
                    charts.net.data.datasets[0].data = netRxPts;
                    charts.net.data.datasets[1].data = netTxPts;
                    charts.net.update('none');
                }
                applyTitle(document.getElementById('netTitle'), 'net', 'Network I/O');
                document.getElementById('netStatus').textContent =
                    `RX: ${avg(data.net_rx).toFixed(2)} MB/s  TX: ${avg(data.net_tx).toFixed(2)} MB/s`;

            } catch (e) {
                console.error('Error loading data:', e);
            }
        }

        const TIME_STEPS = [15, 60, 300, 1200, 3600, 14400, 43200, 86400, 259200];
        const TIME_LABELS = ['15s', '1m', '5m', '20m', '1h', '4h', '12h', '24h', '72h'];
        const slider = document.getElementById('timeSlider');
        const sliderLabel = document.getElementById('sliderLabel');

        function setSliderIndex(idx) {
            slider.value = idx;
            sliderLabel.textContent = TIME_LABELS[idx];
            currentRange = TIME_STEPS[idx];
        }

        slider.addEventListener('input', () => {
            const idx = parseInt(slider.value);
            sliderLabel.textContent = TIME_LABELS[idx];
        });
        slider.addEventListener('change', () => {
            const idx = parseInt(slider.value);
            setSliderIndex(idx);
            loadData(currentRange, true);
        });

        // Default to 5 min (index 2)
        setSliderIndex(2);

        // Refresh: ask the server to re-scan mounted drives right now, then reload.
        document.getElementById('refreshBtn').addEventListener('click', async (e) => {
            const btn = e.currentTarget;
            btn.disabled = true;
            try {
                await fetch('/api/rescan');
            } catch (err) { /* reload anyway */ }
            location.reload();
        });

        // Make the fixed device titles (CPU / RAM / Network) editable.
        ensureEditable(document.getElementById('cpuTitle'), document.getElementById('cpuReset'));
        ensureEditable(document.getElementById('ramTitle'), document.getElementById('ramReset'));
        ensureEditable(document.getElementById('netTitle'), document.getElementById('netReset'));

        // Initial load and auto-refresh every 2 seconds
        loadData(currentRange, true);
        setInterval(() => loadData(currentRange), 2000);
    </script>
</body>
</html>
'''


# Global metrics store
metrics_store = MetricsStore()


class RequestHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            peer_link = (f'<a class="peer-link" href="{_PEER_URL}">&#8594; {_PEER_NAME}</a>'
                         if _PEER_URL else '')
            page = HTML_TEMPLATE.replace('__HOSTNAME__', _HOSTNAME).replace('__PEER_LINK__', peer_link)
            self.wfile.write(page.encode('utf-8'))

        elif path == '/api/rescan':
            metrics_store.rescan()
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode('utf-8'))

        elif path == '/api/metrics':
            seconds = int(query.get('seconds', ['300'])[0])
            data = metrics_store.get_data(seconds)
            if not data:
                self.send_response(503)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "No data available"}).encode('utf-8'))
            else:
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(data).encode('utf-8'))

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == '/api/rename':
            try:
                length = int(self.headers.get('Content-Length', 0) or 0)
                payload = json.loads(self.rfile.read(length) or b'{}')
                key = str(payload.get('key', ''))[:64]
                name = str(payload.get('name', ''))[:64].strip()
            except (ValueError, TypeError):
                key, name = '', ''
            if key:
                metrics_store.set_name(key, name)
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True}).encode('utf-8'))
            else:
                self.send_response(400)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


_PID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web.pid")


def _stop_existing():
    if not os.path.exists(_PID_FILE):
        return
    try:
        with open(_PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.5)
    except (ValueError, OSError):
        pass
    try:
        os.unlink(_PID_FILE)
    except OSError:
        pass


def spawn_background(log_file):
    """Spawn the server in the background using subprocess."""
    script_path = os.path.abspath(__file__)
    _stop_existing()
    with open(log_file, 'a') as log:
        proc = subprocess.Popen(
            [sys.executable, script_path, '--worker'],
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=True
        )
    with open(_PID_FILE, 'w') as f:
        f.write(str(proc.pid))
    print(f"Web UI running in background (PID: {proc.pid})")
    print(f"Web UI: http://localhost:5000")
    print(f"Logs: {log_file}")
    sys.exit(0)


def main():
    foreground = '--foreground' in sys.argv or '-f' in sys.argv
    is_worker = '--worker' in sys.argv

    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_file = os.path.join(script_dir, "web.log")

    if not foreground and not is_worker:
        spawn_background(log_file)

    # Flush history to disk on a clean shutdown (SIGTERM from the restart logic,
    # or Ctrl-C) so we don't lose the interval since the last periodic save.
    def _shutdown(signum, frame):
        metrics_store._save_history()
        sys.exit(0)
    signal.signal(signal.SIGTERM, _shutdown)

    start_collection()
    server = ThreadingHTTPServer(('0.0.0.0', 5000), RequestHandler)
    if is_worker:
        with open(log_file, 'a') as f:
            f.write(f"Web UI started (PID: {os.getpid()})\n")
    else:
        print(f"Web UI running at http://localhost:5000", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        metrics_store._save_history()
        if not is_worker:
            print("\nServer stopped.", flush=True)


def start_collection():
    thread = threading.Thread(target=metrics_store.collect_loop, daemon=True)
    thread.start()


if __name__ == '__main__':
    main()
