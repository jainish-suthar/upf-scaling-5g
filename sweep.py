#!/usr/bin/env python3
"""Load sweep.  usage: python3 sweep.py results/name.csv 1x10 2x10 4x10 ...
Each step NxR = N active UEs, each sending R Mbit/s of UDP through the UPF(s) to the data network.
UEs are added as needed (they stay registered between steps)."""
import csv, json, os, re, statistics, subprocess, sys, time, urllib.request

P, DN, DUR, WARM, COOL = "upf-scaling-5g-", "10.10.3.30", 15, 3, 5
PING_HZ = 5
METRICS = {"upf1": 9091, "upf2": 9092}


def run(*a):
    return subprocess.run(a, capture_output=True, text=True).stdout


def dx(c, *a):
    return ["docker", "exec", P + c + "-1", *a]


def tuns():
    out = run(*dx("ue", "ip", "-o", "-4", "addr", "show"))
    t = re.findall(r"(uesimtun\d+)\s+inet (\S+?)/", out)
    return sorted(t, key=lambda x: int(x[0][8:]))


def ensure_ues(n):
    have = len(tuns())
    if have >= n:
        return
    subprocess.run(["docker", "exec", "-d", P + "ue-1", "./nr-ue", "-c", "/config/ue.yaml",
                    "-i", f"imsi-99970{have + 1:010d}", "-n", str(n - have), "-l"])
    for _ in range(90):
        time.sleep(1)
        if len(tuns()) >= n:
            return
    raise SystemExit(f"only {len(tuns())} of {n} UEs came up")


def running():
    return [c for c in ("upf1", "upf2") if run("docker", "ps", "-q", "-f", f"name={P}{c}-1")]


def cpu_usec(c):
    return int(re.search(r"usage_usec (\d+)", run(*dx(c, "cat", "/sys/fs/cgroup/cpu.stat"))).group(1))


def cap(c):                      # cores allowed by the cgroup quota (None = unlimited)
    q = run(*dx(c, "cat", "/sys/fs/cgroup/cpu.max")).split()
    return None if q[0] == "max" else int(q[0]) / int(q[1])


def devs(c):                     # (N3 device, N6 device) inside a UPF container
    a = run(*dx(c, "ip", "-o", "-4", "addr"))
    return tuple(re.search(r"\d+: (\S+)\s+inet " + re.escape(p), a).group(1) for p in ("10.10.1.", "10.10.3."))


def pkts_in_out(c):              # packets received on N3 / sent on N6
    d3, d6 = devs(c)
    rd = lambda d, k: int(run(*dx(c, "cat", f"/sys/class/net/{d}/statistics/{k}_packets")))
    return rd(d3, "rx"), rd(d6, "tx")


def sessions(c):
    try:
        txt = urllib.request.urlopen(f"http://localhost:{METRICS[c]}/metrics", timeout=2).read().decode()
        return int(re.search(r"^fivegs_upffunction_upf_sessionnbr (\d+)", txt, re.M).group(1))
    except Exception:
        return None


def step(n, rate, upfs):
    ensure_ues(n)
    time.sleep(2)
    ifs = tuns()[:n]
    total = DUR + WARM
    iperfs = [subprocess.Popen(dx("ue", "iperf3", "-u", "-l", "1200", "-B", ip, "-c", DN, "-p", str(5201 + i),
                                  "-b", f"{rate}M", "-t", str(DUR), "-O", str(WARM), "-J"),
                               stdout=subprocess.PIPE, text=True) for i, (_, ip) in enumerate(ifs)]
    ping = subprocess.Popen(dx("ue", "ping", "-I", ifs[0][0], "-i", str(1 / PING_HZ), "-c", str(total * PING_HZ), DN),
                            stdout=subprocess.PIPE, text=True)
    watch = list(upfs) + ["gnb", "ue"]
    io0 = {c: pkts_in_out(c) for c in upfs}
    prev = {c: (time.time(), cpu_usec(c)) for c in watch}
    cpu = {c: [] for c in watch}
    for s in range(total):
        time.sleep(1)
        for c in watch:
            t, u = time.time(), cpu_usec(c)
            cpu[c].append((u - prev[c][1]) / 1e6 / (t - prev[c][0]))      # cores used in this second
            prev[c] = (t, u)
    sess = {c: sessions(c) for c in upfs}
    io1 = {c: pkts_in_out(c) for c in upfs}

    thr = lost = pkts = 0.0
    jit, bad = [], 0
    for p in iperfs:
        try:
            s = json.loads(p.communicate(timeout=30)[0])["end"]["sum"]
            thr += s["bits_per_second"] / 1e6 * (1 - s["lost_packets"] / s["packets"])   # delivered, not sent
            lost += s["lost_packets"]
            pkts += s["packets"]
            jit.append(s["jitter_ms"])
        except Exception:
            bad += 1
    rtts = [float(x) for x in re.findall(r"time=([\d.]+) ms", ping.communicate()[0])]
    sent = total * PING_HZ
    rtts = rtts[WARM * PING_HZ:] or [float("nan")]
    rtts.sort()

    row = {"ues": n, "rate_mbps": rate, "offered_mbps": n * rate, "throughput_mbps": round(thr, 1),
           "loss_pct": round(100 * lost / pkts, 2) if pkts else 100.0,
           "jitter_ms": round(statistics.mean(jit), 3) if jit else "",
           "rtt_avg_ms": round(statistics.mean(rtts), 2), "rtt_p95_ms": round(rtts[int(0.95 * (len(rtts) - 1))], 2),
           "rtt_max_ms": round(rtts[-1], 2), "iperf_failed": bad}
    for c in ("upf1", "upf2"):
        if c in upfs:
            w = cpu[c][WARM:]                                           # drop warm-up seconds
            k = cap(c) or 1
            row[f"{c}_cpu_pct"] = round(100 * statistics.mean(w) / k, 1)
            row[f"{c}_cpu_max_pct"] = round(100 * max(w) / k, 1)
            row[f"{c}_sessions"] = sess[c]
            din, dout = io1[c][0] - io0[c][0], io1[c][1] - io0[c][1]
            row[f"{c}_pkts_in"] = din
            row[f"{c}_inside_loss_pct"] = round(100 * (din - dout) / din, 2) if din else ""
        else:
            row[f"{c}_cpu_pct"] = row[f"{c}_cpu_max_pct"] = row[f"{c}_sessions"] = ""
            row[f"{c}_pkts_in"] = row[f"{c}_inside_loss_pct"] = ""
    row["gnb_cores"] = round(statistics.mean(cpu["gnb"][WARM:]), 2)
    row["ue_cores"] = round(statistics.mean(cpu["ue"][WARM:]), 2)
    return row


def main():
    out, steps = sys.argv[1], [tuple(map(float, s.split("x"))) for s in sys.argv[2:]]
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    new = not os.path.exists(out)
    for n, rate in steps:
        row = step(int(n), rate, running())
        with open(out, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(row))
            if new:
                w.writeheader()
                new = False
            w.writerow(row)
        print(f"{row['ues']:>3} UEs x {row['rate_mbps']:g}M  off={row['offered_mbps']:g}  thr={row['throughput_mbps']}  "
              f"loss={row['loss_pct']}%  rtt={row['rtt_avg_ms']}/{row['rtt_p95_ms']}ms  upf1_cpu={row['upf1_cpu_pct']}%  "
              f"sess={row['upf1_sessions']}  upf_inside_loss={row['upf1_inside_loss_pct']}%  gnb={row['gnb_cores']} ue={row['ue_cores']} cores", flush=True)
        time.sleep(COOL)


main()
