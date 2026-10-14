#!/usr/bin/env python3
"""Load sweep.  usage: python3 sweep.py [-r REPEATS] results/name.csv 1x10 2x10 4x10 ...
Each step NxR = N active UEs, each sending R Mbit/s of UDP through the UPF(s) to the data network.
UEs are added as needed (they stay registered between steps).
With -r N the whole list of steps is run N times (round by round, so slow drift doesn't hit one step only);
every run is one row in name.csv, and name_summary.csv holds mean/std per step over the valid runs.
name_samples.csv holds per-second samples (UPF CPU, throttling, probe RTT) of every run, for plots and controller tuning.
RTT is measured from one extra UE that sends no traffic (the probe), so it shows queueing in the UPF rather than
the sending UE's own backlog; RTT from the first loaded UE is recorded alongside (rtt_loaded_*)."""
import argparse, csv, json, os, platform, re, shutil, statistics, subprocess, sys, time, urllib.request

P, DN, DUR, WARM, COOL = "upf-scaling-5g-", "10.10.3.30", 15, 3, 5
PING_HZ = 5
METRICS = {"upf1": 9091, "upf2": 9092}
SENT_MIN = 0.95                  # generator must send >= 95% of the offered load, else the row is invalid
GNB_MAX = 0.9                    # gNB forwarding near one full core = the simulator, not the UPF, is the limit


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


def check_alive(ifs):            # every UE tunnel must reach the DN before a step (dead after a host suspend)
    pings = [(i, subprocess.Popen(dx("ue", "ping", "-I", i, "-c", "2", "-W", "1", DN),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)) for i, _ in ifs]
    dead = [i for i, p in pings if p.wait()]
    if dead:
        raise SystemExit(f"no connectivity through {', '.join(dead)} (host slept? try: docker compose restart ue)")


def running():
    return [c for c in ("upf1", "upf2") if run("docker", "ps", "-q", "-f", f"name={P}{c}-1")]


# Monitoring must not run inside a UPF container: every `docker exec` there costs ~30 ms of CPU charged to the
# UPF's own cgroup (its whole budget is 2.5 ms per 10 ms period), which throttles the UPF we are measuring.
# So cgroup and interface counters are read from the host; `docker exec` is only a fallback where the host
# cannot see them (Docker Desktop on macOS).
HOST = {}                        # container -> (cgroup dir on the host or None, host pid)


def locate(c):
    cid, pid = run("docker", "inspect", "-f", "{{.Id}} {{.State.Pid}}", P + c + "-1").split()
    for d in (f"/sys/fs/cgroup/system.slice/docker-{cid}.scope", f"/sys/fs/cgroup/docker/{cid}"):
        if os.access(d + "/cpu.stat", os.R_OK):
            HOST[c] = (d, pid)
            return
    HOST[c] = (None, pid)


def cgfile(c, name):
    d = HOST[c][0]
    if d:
        with open(f"{d}/{name}") as f:
            return f.read()
    return run(*dx(c, "cat", f"/sys/fs/cgroup/{name}"))


def cstat(c):                    # cgroup cpu.stat: usage_usec, nr_periods, nr_throttled, throttled_usec, ...
    return {k: int(v) for k, v in (l.split() for l in cgfile(c, "cpu.stat").splitlines())}


def cap(c):                      # cores allowed by the cgroup quota (None = unlimited)
    q = cgfile(c, "cpu.max").split()
    return None if q[0] == "max" else int(q[0]) / int(q[1])


def devs(c):                     # (N3 device, N6 device) inside a UPF container; looked up before traffic starts
    a = run(*dx(c, "ip", "-o", "-4", "addr"))
    return tuple(re.search(r"\d+: (\S+)\s+inet " + re.escape(p), a).group(1) for p in ("10.10.1.", "10.10.3."))


def pkts_in_out(c, d3, d6):      # packets received on N3 / sent on N6
    try:
        with open(f"/proc/{HOST[c][1]}/net/dev") as f:
            dev = {l.split(":")[0].strip(): l.split(":")[1].split() for l in f.read().splitlines()[2:]}
        return int(dev[d3][1]), int(dev[d6][9])
    except OSError:
        rd = lambda d, k: int(run(*dx(c, "cat", f"/sys/class/net/{d}/statistics/{k}_packets")))
        return rd(d3, "rx"), rd(d6, "tx")


def sessions(c):
    try:
        txt = urllib.request.urlopen(f"http://localhost:{METRICS[c]}/metrics", timeout=2).read().decode()
        return int(re.search(r"^fivegs_upffunction_upf_sessionnbr (\d+)", txt, re.M).group(1))
    except Exception:
        return None


def setup():                     # recorded in every row so results from different machines/builds can be told apart
    locate("upf1")
    ver = run(*dx("upf1", "dpkg-query", "-W", "-f=${Version}", "open5gs-upf")).strip() or "unknown"
    return {"host": f"{platform.node()} ({os.cpu_count()} cpu)", "open5gs": ver, "upf_cap_cores": cap("upf1") or "none",
            "monitor": "host" if HOST["upf1"][0] else "docker-exec"}


def rtt_by_seq(out):              # ping output -> {icmp_seq: rtt_ms}
    return {int(a): float(b) for a, b in re.findall(r"icmp_seq=(\d+) .*?time=([\d.]+) ms", out)}


def pct(v, q):
    return v[int(q * (len(v) - 1))]


def step(n, rate, upfs):
    ensure_ues(n + 1)                                       # n traffic UEs + 1 idle probe UE
    time.sleep(2)
    t = tuns()
    ifs, probe = t[:n], t[n]
    check_alive(ifs + [probe])
    watch = list(upfs) + ["gnb", "ue"]
    for c in watch:
        locate(c)
    nd = {c: devs(c) for c in upfs}
    total = DUR + WARM
    iperfs = [subprocess.Popen(dx("ue", "iperf3", "-u", "-l", "1200", "-B", ip, "-c", DN, "-p", str(5201 + i),
                                  "-b", f"{rate}M", "-t", str(DUR), "-O", str(WARM), "-J"),
                               stdout=subprocess.PIPE, text=True) for i, (_, ip) in enumerate(ifs)]
    pings = {k: subprocess.Popen(dx("ue", "ping", "-I", i, "-i", str(1 / PING_HZ), "-c", str(total * PING_HZ), DN),
                                 stdout=subprocess.PIPE, text=True) for k, i in (("probe", probe[0]), ("loaded", ifs[0][0]))}
    io0 = {c: pkts_in_out(c, *nd[c]) for c in upfs}
    prev = {c: (time.time(), cstat(c)) for c in watch}
    cpu = {c: [] for c in watch}
    samples = []
    for s in range(total):
        time.sleep(1)
        smp = {"second": s + 1, "time": time.strftime("%H:%M:%S"), "warmup": int(s < WARM)}
        for c in watch:
            t, st = time.time(), cstat(c)
            (t0, st0), dt = prev[c], t - prev[c][0]
            cpu[c].append((st["usage_usec"] - st0["usage_usec"]) / 1e6 / dt)          # cores used in this second
            if c in upfs:
                per = st["nr_periods"] - st0["nr_periods"]
                smp[f"{c}_cpu_pct"] = round(100 * cpu[c][-1] / (cap(c) or 1), 1)
                smp[f"{c}_throttled_pct"] = round(100 * (st["nr_throttled"] - st0["nr_throttled"]) / per, 1) if per else 0.0
                smp[f"{c}_throttled_ms"] = round((st["throttled_usec"] - st0["throttled_usec"]) / 1e3 / dt, 1)
                if s == WARM - 1:
                    prev[c + "_thr"] = (t, st)                                      # throttle baseline after warm-up
            else:
                smp[f"{c}_cores"] = round(cpu[c][-1], 2)
            prev[c] = (t, st)
        samples.append(smp)
    sess = {c: sessions(c) for c in upfs}
    io1 = {c: pkts_in_out(c, *nd[c]) for c in upfs}
    thr_end = {c: (time.time(), cstat(c)) for c in upfs}

    thr = sent = lost = pkts = 0.0
    jit, bad = [], 0
    for p in iperfs:
        try:
            s = json.loads(p.communicate(timeout=30)[0])["end"]["sum"]
            sent += s["bits_per_second"] / 1e6                                           # what the client sent
            thr += s["bits_per_second"] / 1e6 * (1 - s["lost_packets"] / s["packets"])   # delivered, not sent
            lost += s["lost_packets"]
            pkts += s["packets"]
            jit.append(s["jitter_ms"])
        except Exception:
            bad += 1
    seq = {k: rtt_by_seq(p.communicate()[0]) for k, p in pings.items()}
    for smp in samples:          # probe RTT per second; seq numbers start at 1, PING_HZ per second
        got = [seq["probe"][q] for q in range(smp["second"] * PING_HZ - PING_HZ + 1, smp["second"] * PING_HZ + 1) if q in seq["probe"]]
        smp["probe_rtt_ms"] = round(statistics.mean(got), 2) if got else ""
        smp["probe_lost"] = PING_HZ - len(got)
    after = lambda k: sorted(v for q, v in seq[k].items() if q > WARM * PING_HZ) or [float("nan")]
    rtts, loaded = after("probe"), after("loaded")

    row = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "ues": n, "rate_mbps": rate, "offered_mbps": n * rate,
           "sent_mbps": round(sent, 1), "throughput_mbps": round(thr, 1),
           "loss_pct": round(100 * lost / pkts, 2) if pkts else 100.0,
           "jitter_ms": round(statistics.mean(jit), 3) if jit else "",
           "rtt_avg_ms": round(statistics.mean(rtts), 2), "rtt_p95_ms": round(pct(rtts, 0.95), 2),
           "rtt_max_ms": round(rtts[-1], 2), "probe_lost_pct": round(100 * (1 - sum(q > WARM * PING_HZ for q in seq["probe"]) / (DUR * PING_HZ)), 1),
           "rtt_loaded_avg_ms": round(statistics.mean(loaded), 2), "rtt_loaded_p95_ms": round(pct(loaded, 0.95), 2),
           "iperf_failed": bad}
    for c in ("upf1", "upf2"):
        if c in upfs:
            w = cpu[c][WARM:]                                           # drop warm-up seconds
            k = cap(c) or 1
            (t0, a), (t1, b) = prev[c + "_thr"], thr_end[c]
            periods = b["nr_periods"] - a["nr_periods"]
            row[f"{c}_cpu_pct"] = round(100 * statistics.mean(w) / k, 1)
            row[f"{c}_cpu_max_pct"] = round(100 * max(w) / k, 1)
            # share of 10 ms CPU periods in which the UPF hit its quota and was paused, and paused time per second
            row[f"{c}_throttled_pct"] = round(100 * (b["nr_throttled"] - a["nr_throttled"]) / periods, 1) if periods else 0.0
            row[f"{c}_throttled_ms_per_s"] = round((b["throttled_usec"] - a["throttled_usec"]) / 1e3 / (t1 - t0), 1)
            row[f"{c}_sessions"] = sess[c]
            din, dout = io1[c][0] - io0[c][0], io1[c][1] - io0[c][1]
            row[f"{c}_pkts_in"] = din
            row[f"{c}_inside_loss_pct"] = round(100 * (din - dout) / din, 2) if din else ""
        else:
            for k in ("cpu_pct", "cpu_max_pct", "throttled_pct", "throttled_ms_per_s", "sessions", "pkts_in", "inside_loss_pct"):
                row[f"{c}_{k}"] = ""
    row["gnb_cores"] = round(statistics.mean(cpu["gnb"][WARM:]), 2)
    row["ue_cores"] = round(statistics.mean(cpu["ue"][WARM:]), 2)

    why = []                     # a row only measures the UPF if the generator delivered the offered load
    if bad:
        why.append(f"{bad} iperf3 failed")
    if sent < SENT_MIN * n * rate:
        why.append(f"generator sent {sent:.0f} of {n * rate:g} Mbit/s")
    if row["gnb_cores"] >= GNB_MAX:
        why.append(f"gnb at {row['gnb_cores']} cores")
    row["valid"] = int(not why)
    row["invalid_reason"] = "; ".join(why)
    return row, samples


def summarize(out):              # mean/std per step over valid runs, from everything in the raw CSV
    with open(out, newline="") as f:
        rows = list(csv.DictReader(f))
    keys = ["throughput_mbps", "loss_pct", "rtt_avg_ms", "rtt_p95_ms", "probe_lost_pct", "rtt_loaded_avg_ms",
            "upf1_cpu_pct", "upf1_throttled_pct", "upf1_throttled_ms_per_s", "upf1_inside_loss_pct"]
    groups = {}
    for r in rows:
        groups.setdefault((r["host"], r["open5gs"], r["upf_cap_cores"], int(r["ues"]), float(r["rate_mbps"])), []).append(r)
    res = []
    for (host, ver, capc, n, rate), rs in sorted(groups.items()):
        ok = [r for r in rs if r["valid"] == "1"]
        s = {"host": host, "open5gs": ver, "upf_cap_cores": capc, "ues": n, "rate_mbps": rate,
             "offered_mbps": n * rate, "runs": len(rs), "valid_runs": len(ok)}
        for k in keys:
            v = [float(r[k]) for r in ok if r[k] not in ("", "nan")]
            s[f"{k}_mean"] = round(statistics.mean(v), 2) if v else ""
            s[f"{k}_std"] = round(statistics.stdev(v), 2) if len(v) > 1 else ""
        res.append(s)
    path = os.path.splitext(out)[0] + "_summary.csv"
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(res[0]))
        w.writeheader()
        w.writerows(res)
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("-r", "--repeat", type=int, default=1, help="run the whole step list this many times")
    ap.add_argument("out")
    ap.add_argument("steps", nargs="+", help="NxR = N UEs x R Mbit/s each")
    a = ap.parse_args()

    # keep the host awake (and ignore the lid) while measuring; a suspend kills the UE tunnels mid-sweep
    if not os.environ.get("SWEEP_INHIBITED") and shutil.which("systemd-inhibit"):
        os.environ["SWEEP_INHIBITED"] = "1"
        os.execvp("systemd-inhibit", ["systemd-inhibit", "--what=sleep:idle:handle-lid-switch", "--who=sweep.py",
                                      "--why=UPF load sweep running", sys.executable, *sys.argv])

    steps = [tuple(map(float, s.split("x"))) for s in a.steps]
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    new = not os.path.exists(a.out)
    meta = setup()
    print(f"setup: {meta}", flush=True)
    for r in range(1, a.repeat + 1):
        for n, rate in steps:
            res, samples = step(int(n), rate, running())
            row = {"run": r, **meta, **res}
            if new is not None:  # first row of this invocation: refuse to mix column layouts in one file
                if not new:
                    with open(a.out, newline="") as f:
                        if next(csv.reader(f), None) != list(row):
                            raise SystemExit(f"{a.out} has different columns (older sweep.py?) - use a new file name")
            with open(a.out, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(row))
                if new:
                    w.writeheader()
                w.writerow(row)
            new = None
            sp = os.path.splitext(a.out)[0] + "_samples.csv"
            srows = [{"run": r, "ues": row["ues"], "rate_mbps": row["rate_mbps"], **x} for x in samples]
            fresh = not os.path.exists(sp)
            with open(sp, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(srows[0]))
                if fresh:
                    w.writeheader()
                w.writerows(srows)
            print(f"run {r} {row['ues']:>3} UEs x {row['rate_mbps']:g}M  off={row['offered_mbps']:g}  sent={row['sent_mbps']}  "
                  f"thr={row['throughput_mbps']}  loss={row['loss_pct']}%  rtt={row['rtt_avg_ms']}/{row['rtt_p95_ms']}ms (loaded UE {row['rtt_loaded_avg_ms']})  "
                  f"upf1_cpu={row['upf1_cpu_pct']}%  throttled={row['upf1_throttled_pct']}%  inside_loss={row['upf1_inside_loss_pct']}%  sess={row['upf1_sessions']}  "
                  f"gnb={row['gnb_cores']} ue={row['ue_cores']} cores  "
                  f"{'VALID' if row['valid'] else 'INVALID: ' + row['invalid_reason']}", flush=True)
            time.sleep(COOL)
    print(f"summary: {summarize(a.out)}")


main()
