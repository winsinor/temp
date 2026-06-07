#!/usr/bin/env python3
"""
ASCII System Metrics Dashboard
Shows CPU, GPU, and RAM usage over time with Braille sparklines.
Displays temperatures (not graphed).
Adapts to terminal width (minimum 40 characters).
"""

import time
import sys
import os
import subprocess
import shutil
from collections import deque
from pathlib import Path

INTERVAL = 1.0  # seconds between updates
HISTORY_SIZE = 600  # 10 minutes of data at 1-second intervals

# Braille characters for sparklines (16 levels for better resolution)
BRAILLE = "⠀⠁⠂⠃⠄⠅⠆⠇⠈⠉⠊⠋⠌⠍⠎⠏"


class MetricsCollector:
    """Collects system metrics (CPU, GPU, RAM, temperatures)."""

    def __init__(self):
        self.prev_idle, self.prev_total = self._read_cpu_stat()
        self.has_nvidia = shutil.which("nvidia-smi") is not None
        self.gpu_count = 0
        if self.has_nvidia:
            self._detect_gpu_count()

    def _read_cpu_stat(self):
        """Read CPU stats from /proc/stat."""
        try:
            with open("/proc/stat") as f:
                parts = f.readline().split()
            vals = [int(x) for x in parts[1:]]
            idle = vals[3]
            total = sum(vals)
            return idle, total
        except (FileNotFoundError, ValueError):
            return 0, 0

    def _detect_gpu_count(self):
        """Detect number of NVIDIA GPUs available."""
        try:
            output = subprocess.check_output(
                ["nvidia-smi", "--list-gpus"],
                stderr=subprocess.DEVNULL,
                timeout=5,
                text=True
            ).strip()
            self.gpu_count = len(output.split('\n')) if output else 0
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            self.gpu_count = 0

    def _get_cpu_usage(self):
        """Calculate CPU usage percentage."""
        idle, total = self._read_cpu_stat()
        d_idle = idle - self.prev_idle
        d_total = total - self.prev_total
        self.prev_idle, self.prev_total = idle, total

        if d_total > 0:
            return 100.0 * (1.0 - d_idle / d_total)
        return 0.0

    def _get_cpu_temp(self):
        """Get CPU temperature in Celsius."""
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                return float(f.read()) / 1000.0
        except (FileNotFoundError, ValueError):
            return None

    def _get_gpu_stats(self):
        """Get individual GPU usage, temperature, and VRAM usage."""
        if not self.has_nvidia or self.gpu_count == 0:
            return [], [], []

        try:
            output = subprocess.check_output(
                ["nvidia-smi",
                 "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                stderr=subprocess.DEVNULL,
                timeout=5,
                text=True
            ).strip()

            gpus_usage = []
            gpus_temp = []
            gpus_mem_pct = []
            for line in output.split('\n'):
                parts = [x.strip() for x in line.split(',')]
                if len(parts) >= 4:
                    gpus_usage.append(float(parts[0]))
                    gpus_temp.append(float(parts[1]))
                    mem_used = float(parts[2])
                    mem_total = float(parts[3])
                    gpus_mem_pct.append(
                        100.0 * mem_used / mem_total if mem_total > 0 else 0.0
                    )

            return gpus_usage, gpus_temp, gpus_mem_pct
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                ValueError, FileNotFoundError):
            return [], [], []

    def _get_ram_usage(self):
        """Get system RAM usage percentage."""
        try:
            with open("/proc/meminfo") as f:
                meminfo = {}
                for line in f:
                    key, val = line.split(':')
                    meminfo[key.strip()] = int(val.split()[0])

            mem_total = meminfo.get("MemTotal", 1)
            mem_available = meminfo.get("MemAvailable", 0)
            mem_used = mem_total - mem_available
            return 100.0 * mem_used / mem_total
        except (FileNotFoundError, ValueError, KeyError):
            return 0.0

    def collect(self):
        """Collect all metrics."""
        gpus_usage, gpus_temp, gpus_mem_pct = self._get_gpu_stats()

        return {
            "cpu": self._get_cpu_usage(),
            "cpu_temp": self._get_cpu_temp(),
            "gpu_usage": gpus_usage,
            "gpu_temp": gpus_temp,
            "gpu_mem_pct": gpus_mem_pct,
            "ram": self._get_ram_usage(),
        }


def braille_sparkline(values, lo, hi, width):
    """Generate a Braille sparkline from values."""
    values_to_use = list(values)[-width:] if len(values) > 0 else []

    out = []
    for v in values_to_use:
        norm = (v - lo) / (hi - lo) if hi > lo else 0
        norm = max(0.0, min(1.0, norm))
        idx = int(norm * (len(BRAILLE) - 1))
        out.append(BRAILLE[idx])

    # Pad with spaces if we don't have enough data
    if len(out) < width:
        out = [BRAILLE[0]] * (width - len(out)) + out

    return "".join(out)


def get_terminal_width():
    """Get terminal width, minimum 40."""
    try:
        width = shutil.get_terminal_size().columns
        return max(40, width)
    except:
        return 60


def format_metric_line(label, value, sparkline_str, unit="%"):
    """Format a single metric line."""
    if value is None:
        return f"{label:<12} {'N/A':>6}"
    return f"{label:<12} {value:>6.1f}{unit}  │{sparkline_str}"


def main():
    collector = MetricsCollector()

    # Data buffers (deques for efficient append/pop)
    cpu_data = deque(maxlen=HISTORY_SIZE)
    ram_data = deque(maxlen=HISTORY_SIZE)

    # GPU data buffers - one per GPU
    gpu_data = [deque(maxlen=HISTORY_SIZE) for _ in range(4)]  # Up to 4 GPUs
    gpu_mem_data = [deque(maxlen=HISTORY_SIZE) for _ in range(4)]

    # Hide cursor and clear screen
    sys.stdout.write("\033[?25l\033[2J\033[H")
    sys.stdout.flush()

    try:
        while True:
            metrics = collector.collect()

            cpu_data.append(metrics["cpu"])
            ram_data.append(metrics["ram"])

            # Store GPU data by GPU index
            for i, usage in enumerate(metrics["gpu_usage"]):
                if i < len(gpu_data):
                    gpu_data[i].append(usage)
            for i, mem_pct in enumerate(metrics["gpu_mem_pct"]):
                if i < len(gpu_mem_data):
                    gpu_mem_data[i].append(mem_pct)

            # Get current terminal width for sparklines
            width = get_terminal_width()
            # Reserve space for label, value, unit, and separator
            sparkline_width = max(20, width - 26)

            # Generate sparklines
            cpu_spark = braille_sparkline(cpu_data, 0, 100, sparkline_width)
            ram_spark = braille_sparkline(ram_data, 0, 100, sparkline_width)

            # Build display
            sys.stdout.write("\033[H\033[J")  # Home and clear to end of display

            # Header
            print("╭─ System Metrics Dashboard")
            print()

            # CPU
            cpu_line = format_metric_line("CPU", metrics["cpu"], cpu_spark)
            print(cpu_line)
            if metrics["cpu_temp"] is not None:
                print(f"  Temp: {metrics['cpu_temp']:.1f}°C")
            print()

            # GPU (if available)
            if collector.has_nvidia and collector.gpu_count > 0:
                for i, (usage, temp) in enumerate(
                    zip(metrics["gpu_usage"], metrics["gpu_temp"])
                ):
                    if i < len(gpu_data) and gpu_data[i]:
                        gpu_spark = braille_sparkline(
                            gpu_data[i], 0, 100, sparkline_width
                        )
                        gpu_line = format_metric_line(f"GPU {i}", usage, gpu_spark)
                        print(gpu_line)
                        if i < len(gpu_mem_data) and gpu_mem_data[i]:
                            mem_pct = metrics["gpu_mem_pct"][i]
                            mem_spark = braille_sparkline(
                                gpu_mem_data[i], 0, 100, sparkline_width
                            )
                            print(format_metric_line(f"  VRAM {i}", mem_pct, mem_spark))
                        print(f"  Temp: {temp:.1f}°C")
                        print()

            # RAM
            ram_line = format_metric_line("RAM", metrics["ram"], ram_spark)
            print(ram_line)
            print()

            # Footer
            print(f"╰─ Samples: {len(cpu_data)} │ Ctrl+C to exit")

            sys.stdout.flush()
            time.sleep(INTERVAL)

    except KeyboardInterrupt:
        sys.stdout.write("\033[?25h")  # Show cursor
        print("\nDashboard stopped.")
    except Exception as e:
        sys.stdout.write("\033[?25h")  # Show cursor
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
