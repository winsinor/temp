#!/usr/bin/env python3
"""
ASCII System Metrics Dashboard
Shows CPU, GPU, and RAM usage over time with sparklines.
Adapts to terminal width (minimum 40 characters).
"""

import time
import sys
import os
import subprocess
import shutil
from collections import deque

INTERVAL = 1.0  # seconds between updates
HISTORY_SIZE = 600  # 10 minutes of data at 1-second intervals

BLOCKS = " ▁▂▃▄▅▆▇█"

class MetricsCollector:
    """Collects system metrics (CPU, GPU, RAM)."""

    def __init__(self):
        self.prev_idle = 0
        self.prev_total = 0
        self._read_cpu_stat()  # Initialize
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
                text=True
            ).strip()
            self.gpu_count = len(output.split('\n')) if output else 0
        except (subprocess.CalledProcessError, FileNotFoundError):
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

    def _get_gpu_usage(self):
        """Get combined GPU usage from all GPUs."""
        if not self.has_nvidia or self.gpu_count == 0:
            return None

        try:
            output = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                stderr=subprocess.DEVNULL,
                text=True
            ).strip()

            gpus = [float(x.strip()) for x in output.split('\n')]
            # Return average GPU usage
            return sum(gpus) / len(gpus) if gpus else 0.0
        except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
            return None

    def _get_gpu_memory(self):
        """Get combined GPU memory usage from all GPUs."""
        if not self.has_nvidia or self.gpu_count == 0:
            return None, None

        try:
            output = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                stderr=subprocess.DEVNULL,
                text=True
            ).strip()

            lines = output.split('\n')
            total_used = 0
            total_mem = 0
            for line in lines:
                parts = [x.strip() for x in line.split(',')]
                if len(parts) >= 2:
                    total_used += float(parts[0])
                    total_mem += float(parts[1])

            if total_mem > 0:
                return 100.0 * total_used / total_mem, total_used
            return None, None
        except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
            return None, None

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
        """Collect all metrics. Returns dict with cpu, gpu, gpu_mem, ram."""
        return {
            "cpu": self._get_cpu_usage(),
            "gpu": self._get_gpu_usage(),
            "gpu_mem": self._get_gpu_memory()[0],
            "ram": self._get_ram_usage(),
        }


def sparkline(values, lo, hi, width):
    """Generate a sparkline from values."""
    values_to_use = list(values)[-width:] if len(values) > 0 else []

    out = []
    for v in values_to_use:
        norm = (v - lo) / (hi - lo) if hi > lo else 0
        norm = max(0.0, min(1.0, norm))
        out.append(BLOCKS[int(norm * (len(BLOCKS) - 1))])

    # Pad with spaces if we don't have enough data
    if len(out) < width:
        out = [' '] * (width - len(out)) + out

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
    gpu_data = deque(maxlen=HISTORY_SIZE)
    gpu_mem_data = deque(maxlen=HISTORY_SIZE)
    ram_data = deque(maxlen=HISTORY_SIZE)

    # Hide cursor and clear screen
    sys.stdout.write("\033[?25l\033[2J\033[H")
    sys.stdout.flush()

    try:
        while True:
            metrics = collector.collect()

            cpu_data.append(metrics["cpu"])
            if metrics["gpu"] is not None:
                gpu_data.append(metrics["gpu"])
            if metrics["gpu_mem"] is not None:
                gpu_mem_data.append(metrics["gpu_mem"])
            ram_data.append(metrics["ram"])

            # Get current terminal width for sparklines
            width = get_terminal_width()
            # Reserve space for label, value, unit, and separator
            # Format: "Label        Value    │sparkline"
            sparkline_width = max(20, width - 24)

            # Generate sparklines
            cpu_spark = sparkline(cpu_data, 0, 100, sparkline_width)
            ram_spark = sparkline(ram_data, 0, 100, sparkline_width)
            gpu_spark = sparkline(gpu_data, 0, 100, sparkline_width) if gpu_data else ""
            gpu_mem_spark = sparkline(gpu_mem_data, 0, 100, sparkline_width) if gpu_mem_data else ""

            # Build display
            sys.stdout.write("\033[H\033[J")  # Home and clear to end of display

            # Header
            title = "╭─ System Metrics"
            print(f"{title}")

            # CPU
            cpu_line = format_metric_line("CPU Usage", metrics["cpu"], cpu_spark)
            print(cpu_line)
            print(f"{'':12}  ▁=0%   █=100%")

            # GPU (if available)
            if collector.has_nvidia and collector.gpu_count > 0:
                print()
                gpu_line = format_metric_line("GPU Usage", metrics["gpu"], gpu_spark)
                print(gpu_line)
                print(f"{'':12}  ▁=0%   █=100%")

                if metrics["gpu_mem"] is not None:
                    print()
                    gpu_mem_line = format_metric_line("GPU RAM", metrics["gpu_mem"], gpu_mem_spark)
                    print(gpu_mem_line)
                    print(f"{'':12}  ▁=0%   █=100%")

            # RAM
            print()
            ram_line = format_metric_line("RAM Usage", metrics["ram"], ram_spark)
            print(ram_line)
            print(f"{'':12}  ▁=0%   █=100%")

            # Footer
            print()
            uptime_str = f"Collected: {len(cpu_data)} samples"
            print(f"╰─ {uptime_str}")
            print("Ctrl+C to exit")

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
