import time
import multiprocessing
import sys
import threading
import math
import psutil
import plotext as plt
import pyfiglet
from sshkeyboard import listen_keyboard, stop_listening


def get_temp():
    with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
        return float(f.read()) / 1000.0


def get_cpu_load():
    return psutil.cpu_percent(interval=None)


class Dashboard:
    def __init__(self):
        self.start_time = time.time()
        self.times = []
        self.temps = []
        self.loads = []
        self.running = True
        # seed psutil so first reading isn't 0
        psutil.cpu_percent(interval=None)

    def update(self):
        elapsed = time.time() - self.start_time
        self.times.append(elapsed)
        self.temps.append(get_temp())
        self.loads.append(get_cpu_load())
        # keep 5 minutes at 0.2s = 1500 samples
        if len(self.times) > 1500:
            self.times.pop(0)
            self.temps.pop(0)
            self.loads.pop(0)

    def draw(self):
        temp = self.temps[-1] if self.temps else 0.0
        load = self.loads[-1] if self.loads else 0.0

        plt.clear_figure()
        plt.subplots(1, 1)

        # Temperature trace (left axis, 40-95)
        plt.plot(self.times, self.temps, marker="braille", color="red", label="Temp (C)")

        # CPU load trace scaled to the temp axis so both fit on one plot
        # 0-100% mapped to 40-95 visually, but we show real % values via label
        scaled_loads = [40 + (l / 100.0) * 55 for l in self.loads]
        plt.plot(self.times, scaled_loads, marker="braille", color="cyan", label="CPU %")

        plt.title("CPU Temperature (red) & Load % (cyan)")
        plt.ylim(40, 95)
        plt.yticks([40, 51, 62, 73, 84, 95], ["40C/0%", "60C/20%", "71C/40%", "82C/60%", "93C/80%", "95C/100%"])
        plt.plotsize(100, 20)
        plt.theme("dark")

        raw_ascii = pyfiglet.figlet_format(f"{temp:.1f} C", font="standard")
        clean_ascii = "".join([f"{line}\033[K\n" for line in raw_ascii.splitlines()])

        sys.stdout.write("\033[H")
        plt.show()
        print("\n" + clean_ascii)
        print(f"  CPU Load: {load:.1f}%\033[K")
        print(f"  Elapsed:  {self.times[-1]:.0f}s\033[K")
        print(f"\033[K")
        print(f"  [q] or [esc] to quit\033[K")
        sys.stdout.flush()


def update_loop(dashboard):
    while dashboard.running:
        dashboard.update()
        dashboard.draw()
        time.sleep(0.2)


if __name__ == "__main__":
    db = Dashboard()
    print("\033[2J\033[H", end="")

    render_thread = threading.Thread(target=update_loop, args=(db,), daemon=True)
    render_thread.start()

    def on_press(key):
        if key in ("q", "esc"):
            db.running = False
            stop_listening()

    try:
        listen_keyboard(on_press=on_press, sleep=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        db.running = False
        render_thread.join(timeout=2.0)
        print("\033[K\nDone.\033[K")
