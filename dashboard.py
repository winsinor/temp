#!/usr/bin/env python3
"""Claude Metrics — full-screen dashboard with Claude color palette."""

import time
import sys
import re
import subprocess
import shutil
from collections import deque
from datetime import datetime

INTERVAL     = 1.0
HISTORY_SIZE = 600

# ── ANSI helpers ─────────────────────────────────────────────────────────────
RST = "\033[0m"
BLD = "\033[1m"


def fg(r, g, b):
    return f"\033[38;2;{r};{g};{b}m"


C_OR  = fg(232, 121,  77)   # Claude orange
C_AM  = fg(255, 155,  55)   # amber
C_HOT = fg(255,  70,  35)   # red-orange (critical)
C_PU  = fg(140,  80, 245)   # Claude purple (low usage)
C_BD  = fg( 60,  44,  98)   # dim border
C_DM  = fg(110,  90, 140)   # muted secondary text
C_TL  = fg(215, 135,  85)   # label / title

BRAILLE = "⠀⠁⠂⠃⠄⠅⠆⠇⠈⠉⠊⠋⠌⠍⠎⠏"
ANSI_RE = re.compile(r'\033\[[0-9;]*m')
DISK_RE = re.compile(r'^(sd[a-z]+|nvme\d+n\d+|mmcblk\d+|vd[a-z]+|xvd[a-z]+|hd[a-z])$')


def vlen(s):
    """Visual length of s, ignoring ANSI escape codes."""
    return len(ANSI_RE.sub('', s))


def val_col(pct):
    if pct < 50:  return C_PU
    if pct < 75:  return C_OR
    if pct < 90:  return C_AM
    return C_HOT


def sparkline(values, lo, hi, width):
    """Colored Braille sparkline of the given visual width."""
    vals = list(values)[-width:]
    pad  = max(0, width - len(vals))
    out  = C_BD + BRAILLE[0] * pad + RST
    for v in vals:
        norm = max(0.0, min(1.0, (v - lo) / (hi - lo) if hi > lo else 0.0))
        out += val_col(norm * 100) + BRAILLE[int(norm * (len(BRAILLE) - 1))] + RST
    return out


def term_size():
    try:
        s = shutil.get_terminal_size()
        return max(40, s.columns), max(10, s.lines)
    except Exception:
        return 80, 24


# ── MetricsCollector ─────────────────────────────────────────────────────────
class MetricsCollector:

    def __init__(self):
        self.prev_idle, self.prev_total = self._read_cpu_stat()
        self.has_nvidia = shutil.which("nvidia-smi") is not None
        self.gpu_count  = 0
        if self.has_nvidia:
            self._detect_gpu_count()
        self._prev_disk   = {}
        self._prev_disk_t = time.monotonic()

    # -- CPU ------------------------------------------------------------------
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
            return None

    # -- GPU ------------------------------------------------------------------
    def _detect_gpu_count(self):
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--list-gpus"],
                stderr=subprocess.DEVNULL, timeout=5, text=True,
            ).strip()
            self.gpu_count = len(out.split('\n')) if out else 0
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            self.gpu_count = 0

    def _get_gpu_stats(self):
        if not self.has_nvidia or self.gpu_count == 0:
            return [], [], []
        try:
            out = subprocess.check_output(
                ["nvidia-smi",
                 "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                stderr=subprocess.DEVNULL, timeout=5, text=True,
            ).strip()
            usage, temps, mem = [], [], []
            for line in out.split('\n'):
                p = [x.strip() for x in line.split(',')]
                if len(p) >= 4:
                    usage.append(float(p[0]))
                    temps.append(float(p[1]))
                    mu, mt = float(p[2]), float(p[3])
                    mem.append(100.0 * mu / mt if mt > 0 else 0.0)
            return usage, temps, mem
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                ValueError, FileNotFoundError):
            return [], [], []

    # -- RAM ------------------------------------------------------------------
    def _get_ram(self):
        try:
            with open("/proc/meminfo") as f:
                mi = {}
                for line in f:
                    k, v = line.split(':')
                    mi[k.strip()] = int(v.split()[0])
            total_kb = mi.get("MemTotal", 1)
            avail_kb = mi.get("MemAvailable", 0)
            used_kb  = total_kb - avail_kb
            return (100.0 * used_kb / total_kb,
                    total_kb / 1048576,
                    used_kb  / 1048576)
        except (FileNotFoundError, ValueError, KeyError):
            return 0.0, 0.0, 0.0

    # -- Disk -----------------------------------------------------------------
    def _get_disk_io(self):
        now = time.monotonic()
        dt  = now - self._prev_disk_t
        try:
            with open("/proc/diskstats") as f:
                curr = {}
                for line in f:
                    parts = line.split()
                    if len(parts) >= 14 and DISK_RE.match(parts[2]):
                        # fields 5 and 9 are sectors_read and sectors_written
                        curr[parts[2]] = (int(parts[5]), int(parts[9]))

            r_mb = w_mb = 0.0
            if self._prev_disk and dt > 0:
                for name, (sr, sw) in curr.items():
                    if name in self._prev_disk:
                        pr, pw = self._prev_disk[name]
                        r_mb += (sr - pr) * 512 / (dt * 1048576)
                        w_mb += (sw - pw) * 512 / (dt * 1048576)

            self._prev_disk   = curr
            self._prev_disk_t = now
            return max(0.0, r_mb), max(0.0, w_mb)
        except (FileNotFoundError, ValueError, OSError):
            self._prev_disk_t = now
            return 0.0, 0.0

    # -- collect --------------------------------------------------------------
    def collect(self):
        gpu_usage, gpu_temp, gpu_mem = self._get_gpu_stats()
        ram_pct, ram_gb, ram_used_gb = self._get_ram()
        disk_r, disk_w               = self._get_disk_io()
        return {
            "cpu":       self._get_cpu_usage(),
            "cpu_temp":  self._get_cpu_temp(),
            "gpu_usage": gpu_usage,
            "gpu_temp":  gpu_temp,
            "gpu_mem":   gpu_mem,
            "ram":       ram_pct,
            "ram_gb":    ram_gb,
            "ram_used":  ram_used_gb,
            "disk_r":    disk_r,
            "disk_w":    disk_w,
        }


# ── Frame builder ─────────────────────────────────────────────────────────────

def build_frame(metrics, hist, W, sample_n):
    """Return a list of display lines for the current frame."""
    # Row layout (visual widths):
    #   "  label___  value_____  extra_____  │sparkline│"
    #    2+8       + 2+9       + 2+9        + 1+SW+1   = 34 + SW = W
    SPARK_W = max(8, W - 34)
    divider = f"{C_BD}{'─' * W}{RST}"

    def mline(label, pct_col, val_str, extra, spark_data, lo=0.0, hi=100.0):
        col = val_col(pct_col)
        lbl = f"  {C_TL}{label:<8}{RST}"
        val = f"  {col}{BLD}{val_str:<9}{RST}"
        ext = f"  {C_DM}{extra:<9}{RST}" if extra else " " * 11
        sp  = sparkline(spark_data, lo, hi, SPARK_W)
        return f"{lbl}{val}{ext}{C_BD}│{RST}{sp}{C_BD}│{RST}"

    lines = []

    # title bar
    now_s = datetime.now().strftime("%a %b %d  %H:%M:%S")
    logo  = f" {C_PU}{BLD}◆{RST} {C_TL}{BLD}CLAUDE METRICS{RST}"
    clock = f"{C_DM}{now_s}{RST} "
    gap   = max(0, W - vlen(logo) - vlen(clock))
    lines.append(logo + " " * gap + clock)
    lines.append(divider)

    # CPU
    t = metrics["cpu_temp"]
    lines.append(mline("CPU", metrics["cpu"], f"{metrics['cpu']:5.1f}%",
                        f"{t:.0f}°C" if t else "---", hist["cpu"]))

    # RAM
    lines.append(mline("RAM", metrics["ram"], f"{metrics['ram']:5.1f}%",
                        f"{metrics['ram_used']:.1f}/{metrics['ram_gb']:.0f}G",
                        hist["ram"]))

    lines.append(divider)

    # GPUs
    for i, (u, t, m) in enumerate(
        zip(metrics["gpu_usage"], metrics["gpu_temp"], metrics["gpu_mem"])
    ):
        lines.append(mline(f"GPU {i}", u, f"{u:5.1f}%",
                            f"{t:.0f}°C", hist["gpu"][i]))
        lines.append(mline(f"  VRAM{i}", m, f"{m:5.1f}%", "",
                            hist["gpu_mem"][i]))

    if metrics["gpu_usage"]:
        lines.append(divider)

    # Disk I/O
    r, w = metrics["disk_r"], metrics["disk_w"]
    hi_d = max(max(hist["disk_r"], default=1.0),
               max(hist["disk_w"], default=1.0), 1.0)
    lines.append(mline("Disk R", 100.0 * r / hi_d,
                        f"{r:6.2f}", "MB/s", hist["disk_r"], 0.0, hi_d))
    lines.append(mline("Disk W", 100.0 * w / hi_d,
                        f"{w:6.2f}", "MB/s", hist["disk_w"], 0.0, hi_d))

    lines.append(divider)

    # status bar
    lines.append(
        f"  {C_DM}samples:{RST} {C_OR}{sample_n}{RST}"
        f"  {C_BD}│{RST}  {C_DM}interval:{RST} {C_OR}{INTERVAL:.1f}s{RST}"
        f"  {C_BD}│{RST}  {C_DM}ctrl+c to exit{RST}"
    )

    return lines


# ── Main loop ────────────────────────────────────────────────────────────────

def main():
    collector = MetricsCollector()

    hist = {
        "cpu":     deque(maxlen=HISTORY_SIZE),
        "ram":     deque(maxlen=HISTORY_SIZE),
        "gpu":     [deque(maxlen=HISTORY_SIZE) for _ in range(4)],
        "gpu_mem": [deque(maxlen=HISTORY_SIZE) for _ in range(4)],
        "disk_r":  deque(maxlen=HISTORY_SIZE),
        "disk_w":  deque(maxlen=HISTORY_SIZE),
    }

    sys.stdout.write("\033[?25l\033[2J")  # hide cursor, clear screen
    sys.stdout.flush()

    try:
        n = 0
        while True:
            m = collector.collect()
            n += 1

            hist["cpu"].append(m["cpu"])
            hist["ram"].append(m["ram"])
            for i, u in enumerate(m["gpu_usage"]):
                if i < 4:
                    hist["gpu"][i].append(u)
            for i, v in enumerate(m["gpu_mem"]):
                if i < 4:
                    hist["gpu_mem"][i].append(v)
            hist["disk_r"].append(m["disk_r"])
            hist["disk_w"].append(m["disk_w"])

            W, _ = term_size()
            frame = build_frame(m, hist, W, n)

            out = ["\033[H"]
            for line in frame:
                pad = max(0, W - vlen(line))
                out.append(line + " " * pad + "\n")
            out.append("\033[J")  # clear anything below

            sys.stdout.write("".join(out))
            sys.stdout.flush()
            time.sleep(INTERVAL)

    except KeyboardInterrupt:
        sys.stdout.write("\033[?25h\033[J")
        print("\nDashboard stopped.")
    except Exception as e:
        sys.stdout.write("\033[?25h")
        print(f"\nError: {e}")


if __name__ == "__main__":
    main()
