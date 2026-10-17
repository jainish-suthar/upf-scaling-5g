#!/usr/bin/env python3
"""Load-aware UPF scaler (1 -> 2 UPFs and back).   usage: python3 controller.py [log.csv]
Every second it reads each running UPF's CPU (share of its cap) and how often it hit the cap (throttled).
 up   : upf1 over the limit for UP_HOLD s            -> start upf2, wait until the SMF has associated with it
 down : total UPF CPU below the low limit for DOWN_HOLD s -> stop upf2, but only when it has no sessions left
The SMF is configured with both UPFs from the start; "adding" upf2 is just starting it (PFCP association)."""
import csv, os, re, subprocess, sys, time
from collections import deque
from sweep import P, cap, cpu_stat, run, running, sessions

# From results/threshold_q1000.csv (UPF_QUOTA=1000 = 0.1 core, 4 UEs, 4 shuffled runs per load, medians): loss first rises above
# light-load level at 60 Mbit/s offered (2.8%, all 4 runs > 1.3%) and delivered throughput flattens at ~69 Mbit/s at 80 offered;
# there the UPF sits at ~92% of its cap and 70-82% of 10 ms periods are throttled. Scale-up takes ~8 s (UP_HOLD + ~3 s PFCP
# association) and only helps sessions set up afterwards, so it triggers at ~2/3 of the knee: ~40 Mbit/s, CPU ~80%, ~50% throttled.
# Percentages are of the UPF's own cap: re-derive them for a different UPF_QUOTA.
UP_THROTTLED, UP_CPU, UP_HOLD = 50.0, 80.0, 5     # % of periods throttled / % of cap, seconds
THR_MIN_CPU = 75.0     # throttling only counts when the UPF is actually busy
DOWN_SUM_CPU, DOWN_HOLD = 35.0, 20                # total CPU (% of one cap) of all UPFs; merged onto one UPF it would sit at about half of UP_CPU
COOLDOWN, AVG = 30, 3                             # seconds between actions, seconds of smoothing


def compose(*a):
    return subprocess.run(["docker", "compose", "--profile", "scale", *a], capture_output=True, text=True)


def smf_associated(since):       # has the SMF logged a PFCP association with upf2 since `since` (unix time)?
    log = re.sub(r"\x1b\[[0-9;]*m", "", run("docker", "logs", "--since", str(int(since)), P + "core-1"))
    return bool(re.search(r"PFCP associated.*10\.10\.2\.21", log))


def scale_up():
    t = time.time()
    compose("up", "-d", "--no-deps", "upf2")
    while time.time() - t < 90 and not smf_associated(t - 1):
        time.sleep(1)
    return round(time.time() - t, 1), smf_associated(t - 1)


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    out = sys.argv[1] if len(sys.argv) > 1 else "results/controller.csv"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    new = not os.path.exists(out)
    prev, win = {}, {}
    over = low = 0
    last_action = 0.0
    while True:
        time.sleep(1)
        up = running()
        row = {"t": round(time.time(), 1), "upfs": len(up), "action": ""}
        sig = {}
        for c in up:
            now, u = time.time(), cpu_stat(c)
            if c in prev:
                dt, du = now - prev[c][0], u[0] - prev[c][1][0]
                per, thr = u[1] - prev[c][1][1], u[2] - prev[c][1][2]
                win.setdefault(c, deque(maxlen=AVG)).append((100 * du / 1e6 / dt / cap(c), 100 * thr / per if per else 0))
                sig[c] = tuple(sum(x[i] for x in win[c]) / len(win[c]) for i in (0, 1))      # smoothed (cpu%, throttled%)
            prev[c] = (now, u)
        for c in ("upf1", "upf2"):
            row[f"{c}_cpu"], row[f"{c}_thr"] = (round(sig[c][0], 1), round(sig[c][1], 1)) if c in sig else ("", "")
            row[f"{c}_sessions"] = sessions(c) if c in up else ""
        if "upf1" in sig:
            over = over + 1 if ((sig["upf1"][1] >= UP_THROTTLED and sig["upf1"][0] >= THR_MIN_CPU) or sig["upf1"][0] >= UP_CPU) else 0
            low = low + 1 if sum(v[0] for v in sig.values()) < DOWN_SUM_CPU else 0
            ready = time.time() - last_action > COOLDOWN
            if len(up) == 1 and over >= UP_HOLD and ready:
                secs, ok = scale_up()
                row["action"] = f"scale_up ready_in={secs}s smf_associated={ok}"
                last_action, over, prev, win = time.time(), 0, {}, {}
            elif len(up) == 2 and low >= DOWN_HOLD and ready and not row["upf2_sessions"]:
                compose("stop", "upf2")
                row["action"] = "scale_down"
                last_action, low, prev, win = time.time(), 0, {}, {}
        with open(out, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(row))
            if new:
                w.writeheader()
                new = False
            w.writerow(row)
        print(f"{row['upfs']} UPF  upf1 cpu={row['upf1_cpu']} thr={row['upf1_thr']} s={row['upf1_sessions']}  "
              f"upf2 cpu={row['upf2_cpu']} thr={row['upf2_thr']} s={row['upf2_sessions']}  {row['action']}", flush=True)


if __name__ == "__main__":
    main()
