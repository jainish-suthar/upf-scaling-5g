#!/usr/bin/env python3
"""Load-aware UPF scaler (1 -> 2 UPFs and back).   usage: python3 controller.py [log.csv] [--drain-timeout S] [--empty-down]
Every second it reads each running UPF's CPU (share of its cap) and how often it hit the cap (throttled).
 up   : upf1 over the limit for UP_HOLD s            -> start upf2, wait until the SMF has associated with it
 down : total UPF CPU below the low limit for DOWN_HOLD s -> stop upf2, but only when it has no sessions left
        (with --drain-timeout S: if upf2 still has sessions S s after that, stop it anyway and cut them; the SMF
        notices by missed heartbeats and the UEs re-establish on upf1 with a new IP)
 --empty-down: also stop upf2 once it has had 0 sessions for EMPTY_HOLD s while upf1 alone stays under the scale-up
        limit. An empty upf2 carries nothing, so stopping it moves no load; this skips waiting for the total CPU to fall
The SMF is configured with both UPFs from the start; "adding" upf2 is just starting it (PFCP association)."""
import csv, os, re, subprocess, sys, time
from collections import deque
from sweep import P, cap, cpu_stat, run, running, sessions

# From results/final/threshold.csv (UPF_QUOTA=1000 = 0.1 core, 4 UEs, 4 shuffled runs per load, medians, 40/40 valid, 2026-11-10):
# delivered throughput flattens at ~68 Mbit/s; at 60 Mbit/s throttling (67% of periods) and the RTT tail (p95 3.7 ms) jump, and
# from 70 Mbit/s the UPF is throttled in nearly every period. Scale-up takes UP_HOLD + 0-11 s (the SMF retries the PFCP
# association about every 11 s; 3.3-6.5 s measured) and only helps sessions set up afterwards, so it triggers at ~2/3 of the knee:
# CPU 80% of cap is ~45 Mbit/s (71% at 40, 87% at 50; 30 Mbit/s sits at 58%, so it stays quiet there).
# The earlier calibration (results/linux/, 2026-10-19, slower CPU setting: knee ~40, capacity ~46 Mbit/s) put the same % at the
# same 2/3 of its knee. Percentages are of the UPF's own cap: re-check them for a different UPF_QUOTA or host.
UP_THROTTLED, UP_CPU, UP_HOLD = 50.0, 80.0, 5     # % of periods throttled / % of cap, seconds
THR_MIN_CPU = 75.0     # throttling only counts when the UPF is actually busy
DOWN_SUM_CPU, DOWN_HOLD = 35.0, 20                # total CPU (% of one cap) of all UPFs; merged onto one UPF it would sit at about half of UP_CPU
EMPTY_HOLD = 20                                   # --empty-down: seconds upf2 must stay empty (and upf1 under UP_CPU)
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
    a = sys.argv[1:]
    drain = None
    if "--drain-timeout" in a:
        i = a.index("--drain-timeout")
        drain = float(a[i + 1])
        del a[i:i + 2]
    early = "--empty-down" in a
    if early:
        a.remove("--empty-down")
    out = a[0] if a else "results/controller.csv"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    new = not os.path.exists(out)
    prev, win = {}, {}
    over = low = empty = 0
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
            empty = empty + 1 if len(up) == 2 and row["upf2_sessions"] == 0 and sig["upf1"][0] < UP_CPU else 0
            ready = time.time() - last_action > COOLDOWN
            if len(up) == 1 and over >= UP_HOLD and ready:
                secs, ok = scale_up()
                row["action"] = f"scale_up ready_in={secs}s smf_associated={ok}"
                last_action, over, prev, win = time.time(), 0, {}, {}
            elif early and len(up) == 2 and empty >= EMPTY_HOLD and ready:
                compose("stop", "upf2")
                row["action"] = "scale_down empty"
                last_action, low, empty, prev, win = time.time(), 0, 0, {}, {}
            elif len(up) == 2 and low >= DOWN_HOLD and ready:
                left = row["upf2_sessions"]          # None = metrics unreadable: treat as "still has sessions"
                if left == 0 or (drain is not None and left is not None and low >= DOWN_HOLD + drain):
                    compose("stop", "upf2")
                    row["action"] = "scale_down" if left == 0 else f"scale_down forced sessions_cut={left}"
                    last_action, low, empty, prev, win = time.time(), 0, 0, {}, {}
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
