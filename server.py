import time
import sys
import os

WIDTH = 60       # sparkline width (chars)
INTERVAL = 1.0   # seconds between updates

BLOCKS = " ▁▂▃▄▅▆▇█"

def get_temp():
    with open("/sys/class/thermal/thermal_zone0/temp") as f:
        return float(f.read()) / 1000.0

def read_cpu_stat():
    with open("/proc/stat") as f:
        parts = f.readline().split()
    vals = [int(x) for x in parts[1:]]
    idle = vals[3]
    total = sum(vals)
    return idle, total

def sparkline(values, lo, hi):
    out = []
    for v in values:
        norm = (v - lo) / (hi - lo) if hi > lo else 0
        norm = max(0.0, min(1.0, norm))
        out.append(BLOCKS[int(norm * (len(BLOCKS) - 1))])
    return "".join(out)

temps = []
loads = []
prev_idle, prev_total = read_cpu_stat()

print("\033[2J\033[H", end="")

try:
    while True:
        # CPU load
        idle, total = read_cpu_stat()
        d_idle = idle - prev_idle
        d_total = total - prev_total
        load = 100.0 * (1.0 - d_idle / d_total) if d_total else 0.0
        prev_idle, prev_total = idle, total

        temp = get_temp()
        temps.append(temp)
        loads.append(load)
        if len(temps) > WIDTH:
            temps.pop(0)
            loads.pop(0)

        t_lo, t_hi = 40.0, 95.0
        l_lo, l_hi = 0.0, 100.0

        temp_line = sparkline(temps, t_lo, t_hi)
        load_line = sparkline(loads, l_lo, l_hi)
        pad = WIDTH - len(temps)

        sys.stdout.write("\033[H")
        print(f"  CPU Temp  {temp:.1f} C")
        print(f"  ▁=40C  █=95C")
        print(f"  {' ' * pad}{temp_line}")
        print()
        print(f"  CPU Load  {load:.1f}%")
        print(f"  ▁=0%   █=100%")
        print(f"  {' ' * pad}{load_line}")
        print()
        print("  Ctrl+C to quit")
        sys.stdout.flush()

        time.sleep(INTERVAL)
except KeyboardInterrupt:
    print("\nDone.")
