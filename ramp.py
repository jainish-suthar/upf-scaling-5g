#!/usr/bin/env python3
"""Ramp experiment for the single vs multi-UPF comparison (phase 7).
usage: python3 ramp.py results/name [--ues 8] [--rate 15] [--gap 20] [--hold 60] [--controller]
A new UE registers every GAP s and immediately sends RATE Mbit/s of UDP until the end (HOLD s after the last UE),
so offered load climbs in steps and each new PDU session is placed while the UPFs are already loaded.
Writes name_timeline.csv (per second: offered/delivered, loss, RTT, each UPF's CPU/throttling/sessions, controller
actions) and name_ues.csv (per UE: which UPF its session landed on, delivered throughput, loss, RTT, last second received).
Throughput/loss come from the data network's iperf3 servers, which log every second as it happens, so a UE whose
iperf3 or radio link dies mid-run still contributes the seconds it delivered (iperf3's own end report would be lost).
--controller runs controller.py alongside (log in name_controller.csv). UEs that existed before the run stay idle."""
import csv, os, re, statistics, subprocess, sys, tempfile, threading, time
from sweep import DN, P, cap, cpu_stat, dx, ensure_ues, run, running, sessions, tuns

PING_HZ = 2
SERVER_LINE = re.compile(r"^(\d+) \[\s*\d+\]\s+([\d.]+)-([\d.]+)\s+sec\s+\S+ \S?Bytes\s+([\d.]+) ([KMG ])bits/sec\s+\S+ ms\s+(\d+)/(\d+)", re.M)
UNIT = {"K": 1e-3, "M": 1, "G": 1e3, " ": 1e-6}


def servers():                   # fresh iperf3 servers on dn, one per UE port, each logging every second with a unix timestamp
    run(*dx("ue", "pkill", "-9", "iperf3"))
    run(*dx("dn", "sh", "-c", "pkill -9 iperf3; rm -f /tmp/s*.log; for p in $(seq 5201 5260); do "
                              "iperf3 -s -D -p $p -i 1 --forceflush --timestamps='%s ' --logfile /tmp/s$p.log; done"))


def sampler(t0, stop, rows):     # once a second: each running UPF's CPU (% of cap), throttled periods (%), sessions
    prev, caps = {}, {}
    while not stop.is_set():
        time.sleep(1)
        up = running()
        row = {"sec": int(time.time() - t0), "upfs": len(up), "host_load1": round(os.getloadavg()[0], 2)}
        for c in ("upf1", "upf2"):
            row[f"{c}_cpu"] = row[f"{c}_thr"] = row[f"{c}_sessions"] = ""
            if c not in up:
                prev.pop(c, None)
                caps.pop(c, None)
                continue
            try:
                now, u = time.time(), cpu_stat(c)
            except Exception:        # container is just starting or stopping
                continue
            if c in prev:
                dt, du, per, thr = now - prev[c][0], u[0] - prev[c][1][0], u[1] - prev[c][1][1], u[2] - prev[c][1][2]
                k = caps.setdefault(c, cap(c) or 1)
                row[f"{c}_cpu"] = round(100 * du / 1e6 / dt / k, 1)
                row[f"{c}_thr"] = round(100 * thr / per, 1) if per else ""
            prev[c] = (now, u)
            row[f"{c}_sessions"] = sessions(c)
        rows.append(row)


def main():
    a = sys.argv[1:]
    def opt(k, default, f=int):
        if k not in a:
            return default
        i = a.index(k)
        v = f(a[i + 1])
        del a[i:i + 2]
        return v
    ctl = "--controller" in a
    if ctl:
        a.remove("--controller")
    n, rate, gap, hold = opt("--ues", 8), opt("--rate", 15.0, float), opt("--gap", 20), opt("--hold", 60)
    out = a[0]
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    if ctl and len(running()) > 1:
        raise SystemExit("stop upf2 first: the controller run must start from one UPF")

    servers()
    time.sleep(2)
    t0 = time.time()
    end = t0 + n * gap + hold
    stop, samples = threading.Event(), []
    th = threading.Thread(target=sampler, args=(t0, stop, samples), daemon=True)
    th.start()
    ctrl = subprocess.Popen([sys.executable, "controller.py", f"{out}_controller.csv"], stdout=subprocess.DEVNULL) if ctl else None

    have = {ip for _, ip in tuns()}
    ues = []
    for i in range(n):
        time.sleep(max(0, t0 + i * gap - time.time()))
        before = {c: sessions(c) or 0 for c in running()}
        ensure_ues(len(have) + 1)
        tun, ip = next(t for t in tuns() if t[1] not in have)
        have.add(ip)
        time.sleep(1)
        landed = [c for c in running() if (sessions(c) or 0) > before.get(c, 0)]
        left = int(end - time.time())
        ue = {"n": i + 1, "ue_ip": ip, "upf": "+".join(landed), "start_s": round(time.time() - t0, 1), "upfs_running": len(running())}
        ue["ping_out"] = tempfile.TemporaryFile("w+")      # files, not pipes: a full pipe would stall ping/iperf3
        ue["ping"] = subprocess.Popen(dx("ue", "ping", "-D", "-I", tun, "-i", str(1 / PING_HZ), "-w", str(left), DN),
                                      stdout=ue["ping_out"])
        for attempt in range(1, 4):     # iperf3 sometimes dies at once ("unable to send control message"): start it again
            left = int(end - time.time())
            ue["iperf"] = subprocess.Popen(dx("ue", "timeout", "-s", "KILL", str(left + 15), "iperf3", "-u", "-l", "1200", "-B", ip, "-c", DN,
                                              "-p", str(5201 + i), "-b", f"{rate}M", "-t", str(left)),
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ue["iperf_attempts"] = attempt
            time.sleep(2)
            if ue["iperf"].poll() is None:
                break
        ues.append(ue)
        print(f"{ue['start_s']:>6}s  UE {i + 1} {ip} -> {ue['upf'] or '?'}  ({len(running())} UPF running)", flush=True)

    delivered, lost, pkts, rtt = {}, {}, {}, {}
    for i, ue in enumerate(ues):
        ue["iperf"].wait()
        got = sent = n_lost = secs = 0
        last = None
        lines = [x for x in SERVER_LINE.findall(run(*dx("dn", "cat", f"/tmp/s{5201 + i}.log")))
                 if 0.5 <= float(x[2]) - float(x[1]) <= 1.5]   # skip the closing summary and the short last interval
        # timestamps are whole seconds: anchor the stream once (median of print time - interval end), then place each interval by its offset
        s0 = statistics.median(int(x[0]) - float(x[2]) for x in lines) + 0.5 if lines else 0
        for ts, a, b, rate_, unit, l, tot in lines:
            k, mbps = int(s0 + float(a) - t0), float(rate_) * UNIT[unit]
            delivered[k] = delivered.get(k, 0) + mbps
            lost[k], pkts[k] = lost.get(k, 0) + int(l), pkts.get(k, 0) + int(tot)
            got, n_lost, sent, secs, last = got + mbps, n_lost + int(l), sent + int(tot), secs + 1, k
        ue["delivered_mbps"] = round(got / secs, 2) if secs else 0
        ue["loss_pct"] = round(100 * n_lost / sent, 2) if sent else ""
        ue["seconds_received"], ue["last_rx_s"] = secs, last if last is not None else ""
        ue["stopped_early"] = int(last is None or last < end - t0 - 5)
        times = []
        ue["ping"].wait()
        ue["ping_out"].seek(0)
        for ts, ms in re.findall(r"\[([\d.]+)\].*time=([\d.]+) ms", ue["ping_out"].read()):
            rtt.setdefault(int(float(ts) - t0), []).append(float(ms))
            times.append(float(ms))
        times.sort()
        ue["rtt_avg_ms"] = round(statistics.mean(times), 2) if times else ""
        ue["rtt_p95_ms"] = round(times[int(0.95 * (len(times) - 1))], 2) if times else ""
        ue["ping_replies"] = len(times)
    stop.set()
    th.join()
    actions = {}
    if ctrl:
        ctrl.terminate()
        with open(f"{out}_controller.csv") as f:
            for r in csv.DictReader(f):
                if r["action"] and float(r["t"]) >= t0:
                    actions[int(float(r["t"]) - t0)] = r["action"]

    with open(f"{out}_timeline.csv", "w", newline="") as f:
        w = None
        for s in samples:
            k = s["sec"]
            active = sum(u["start_s"] <= k for u in ues)
            r = {**s, "active_ues": active, "offered_mbps": active * rate, "delivered_mbps": round(delivered.get(k, 0), 1),
                 "loss_pct": round(100 * lost[k] / pkts[k], 2) if pkts.get(k) else "",
                 "rtt_avg_ms": round(statistics.mean(rtt[k]), 2) if k in rtt else "",
                 "action": actions.get(k, ""), "controller": int(ctl)}
            if w is None:
                w = csv.DictWriter(f, fieldnames=list(r))
                w.writeheader()
            w.writerow(r)
    with open(f"{out}_ues.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["n", "ue_ip", "upf", "start_s", "upfs_running", "iperf_attempts", "delivered_mbps", "loss_pct", "seconds_received", "last_rx_s", "stopped_early", "rtt_avg_ms", "rtt_p95_ms", "ping_replies"],
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(ues)
    for u in ues:
        print(f"UE {u['n']:<3} {u['upf'] or '?':<5} start={u['start_s']}s  delivered={u['delivered_mbps']} Mbit/s  loss={u['loss_pct']}%  "
              f"rtt={u['rtt_avg_ms']}/{u['rtt_p95_ms']} ms  last_rx={u['last_rx_s']}s")
    for k, v in sorted(actions.items()):
        print(f"{k}s  {v}")
    rlf = run("docker", "logs", "--since", str(int(t0)), P + "ue-1").lower().count("radio link failure")
    print(f"UEs stopped early: {sum(u['stopped_early'] for u in ues)}/{len(ues)}   UERANSIM radio link failures logged: {rlf}")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
