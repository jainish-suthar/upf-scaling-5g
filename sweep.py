#!/usr/bin/env python3
"""Load sweep.  usage: python3 sweep.py results/name.csv 1x10 2x10 4x10 ...
Each step NxR = N active UEs, each sending R Mbit/s of UDP through the UPF(s) to the data network.
UEs are added as needed (they stay registered between steps)."""
import csv, http.client, json, os, random, re, socket, statistics, subprocess, sys, time, urllib.request

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
                    "-i", f"imsi-99970{have + 1:010d}", "-n", str(n - have)])
    for _ in range(90):
        time.sleep(1)
        if len(tuns()) >= n:
            return
    raise SystemExit(f"only {len(tuns())} of {n} UEs came up")


def running():
    return [c for c in ("upf1", "upf2") if run("docker", "ps", "-q", "-f", f"name={P}{c}-1")]


SOCK = next((p for p in (os.path.expanduser("~/.docker/run/docker.sock"), "/var/run/docker.sock") if os.path.exists(p)), None)


def api(path):                   # Docker Engine API over its unix socket: stats are read by the daemon, not by a process inside the container
    class U(http.client.HTTPConnection):
        def connect(self):
            self.sock = socket.socket(socket.AF_UNIX)
            self.sock.connect(SOCK)
    c = U("localhost", timeout=10)
    c.request("GET", path)
    return json.loads(c.getresponse().read())


def stats(c):
    return api(f"/containers/{P}{c}-1/stats?stream=false&one-shot=true")


def cpu_stat(c):                 # (cpu microseconds used, CFS periods, periods throttled)
    s = stats(c)
    return (s["cpu_stats"]["cpu_usage"]["total_usage"] // 1000,
            s["cpu_stats"]["throttling_data"]["periods"], s["cpu_stats"]["throttling_data"]["throttled_periods"])


def cap(c):                      # cores allowed by the CPU quota (None = unlimited)
    h = api(f"/containers/{P}{c}-1/json")["HostConfig"]
    if h.get("NanoCpus"):
        return h["NanoCpus"] / 1e9
    return h["CpuQuota"] / h["CpuPeriod"] if h.get("CpuQuota") else None


def devs(c):                     # (N3 device, N6 device) inside a UPF container
    a = run(*dx(c, "ip", "-o", "-4", "addr"))
    return tuple(re.search(r"\d+: (\S+)\s+inet " + re.escape(p), a).group(1) for p in ("10.10.1.", "10.10.3."))


_DEVS = {}


def pkts_in_out(c):              # packets received on N3 / sent on N6 (interface names looked up once, counters read via the API)
    d3, d6 = _DEVS.setdefault(c, devs(c))
    n = stats(c)["networks"]
    return n[d3]["rx_packets"], n[d6]["tx_packets"]


def sessions(c):
    try:
        txt = urllib.request.urlopen(f"http://localhost:{METRICS[c]}/metrics", timeout=2).read().decode()
        return int(re.search(r"^fivegs_upffunction_upf_sessionnbr (\d+)", txt, re.M).group(1))
    except Exception:
        return None


def alive(ts):                          # tunnels that really carry traffic (a UE can register yet have a dead data path)
    ps = [(t, subprocess.Popen(dx("ue", "ping", "-I", t[0], "-c", "2", "-W", "2", DN), stdout=subprocess.DEVNULL)) for t in ts]
    return [t for t, p in ps if p.wait() == 0]


HEALED = [0]


def power():
    try:
        return run("pmset", "-g", "batt").split("\n")[0].replace("Now drawing from ", "").strip("' ")
    except Exception:
        return ""


def reset_iperf():                      # hung iperf3 clients/servers (stalled control connection under loss) corrupt later steps
    run(*dx("ue", "pkill", "-9", "iperf3"))
    run(*dx("dn", "sh", "-c", "pkill -9 iperf3; for p in $(seq 5201 5260); do iperf3 -s -D -p $p; done"))


def step(n, rate, upfs, idle=0):
    ensure_ues(n + idle)                # idle UEs hold a PDU session but send nothing
    reset_iperf()
    time.sleep(2)
    good = alive(tuns())
    if len(good) < n:                   # overload can kill UEs' radio links: restart the UE container and re-register
        HEALED[0] += 1                  # recorded per row: a restart reshuffles sessions between UPFs
        subprocess.run(["docker", "restart", P + "ue-1"], capture_output=True)
        for _ in range(90):
            time.sleep(1)
            if tuns():
                break
        ensure_ues(n + idle)
        time.sleep(3)
        good = alive(tuns())
    if len(good) < n:
        raise SystemExit(f"need {n} working UEs, only {len(good)} of {len(tuns())} tunnels carry traffic")
    ifs = good[:n]
    total = DUR + WARM
    iperfs = [subprocess.Popen(dx("ue", "timeout", "-s", "KILL", str(DUR + WARM + 10), "iperf3", "-u", "-l", "1200", "-B", ip, "-c", DN, "-p", str(5201 + i),
                                  "-b", f"{rate}M", "-t", str(DUR), "-O", str(WARM), "-J"),
                               stdout=subprocess.PIPE, text=True) for i, (_, ip) in enumerate(ifs)]
    ping = subprocess.Popen(dx("ue", "ping", "-I", ifs[0][0], "-i", str(1 / PING_HZ), "-c", str(total * PING_HZ), DN),
                            stdout=subprocess.PIPE, text=True)
    watch = list(upfs) + ["gnb", "ue"]
    io0 = {c: pkts_in_out(c) for c in upfs}
    prev = {c: (time.time(), cpu_stat(c)) for c in watch}
    cpu = {c: [] for c in watch}
    thr_per = {c: [] for c in watch}    # (periods, throttled periods) per second
    for s in range(total):
        time.sleep(1)
        for c in watch:
            t, u = time.time(), cpu_stat(c)
            cpu[c].append((u[0] - prev[c][1][0]) / 1e6 / (t - prev[c][0]))      # cores used in this second
            thr_per[c].append((u[1] - prev[c][1][1], u[2] - prev[c][1][2]))
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
           "rtt_max_ms": round(rtts[-1], 2), "iperf_failed": bad, "dead_tunnels": len(tuns()) - len(good),
           "ue_restarts": HEALED[0], "host_load1": round(os.getloadavg()[0], 2), "power": power()}
    for c in ("upf1", "upf2"):
        if c in upfs:
            w = cpu[c][WARM:]                                           # drop warm-up seconds
            k = cap(c) or 1
            row[f"{c}_cpu_pct"] = round(100 * statistics.mean(w) / k, 1)
            row[f"{c}_cpu_max_pct"] = round(100 * max(w) / k, 1)
            per, thr_n = (sum(x[i] for x in thr_per[c][WARM:]) for i in (0, 1))
            row[f"{c}_throttled_pct"] = round(100 * thr_n / per, 1) if per else ""   # share of 10 ms periods that hit the cap
            row[f"{c}_sessions"] = sess[c]
            din, dout = io1[c][0] - io0[c][0], io1[c][1] - io0[c][1]
            row[f"{c}_pkts_in"] = din
            row[f"{c}_inside_loss_pct"] = round(100 * (din - dout) / din, 2) if din else ""
        else:
            row[f"{c}_cpu_pct"] = row[f"{c}_cpu_max_pct"] = row[f"{c}_throttled_pct"] = row[f"{c}_sessions"] = ""
            row[f"{c}_pkts_in"] = row[f"{c}_inside_loss_pct"] = ""
    row["gnb_cores"] = round(statistics.mean(cpu["gnb"][WARM:]), 2)
    row["ue_cores"] = round(statistics.mean(cpu["ue"][WARM:]), 2)
    return row


def main():
    a = sys.argv[1:]
    def opt(k, default):             # --flag value
        if k not in a:
            return default
        i = a.index(k)
        v = int(a[i + 1])
        del a[i:i + 2]
        return v
    idle, reps, rep0, seed = opt("--idle", 0), opt("--reps", 1), opt("--rep0", 0), opt("--shuffle", None)
    out, steps = a[0], [tuple(map(float, s.split("x"))) for s in a[1:]]
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    new = not os.path.exists(out)
    plan = [(r + 1, st) for r in range(reps) for st in steps]
    if seed is not None:                # randomized order removes load-vs-time confounding
        random.Random(seed).shuffle(plan)
    for rep, (n, rate) in plan:
      for attempt in range(1, 4):       # a step with a failed iperf3 client is retried; every attempt is kept, flagged valid=0
        row = {"rep": rep + rep0, "attempt": attempt, "idle_ues": idle, "t": round(time.time(), 1), **step(int(n), rate, running(), idle)}
        row["valid"] = int(row["iperf_failed"] == 0 and row["dead_tunnels"] == 0)
        with open(out, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(row))
            if new:
                w.writeheader()
                new = False
            w.writerow(row)
        print(f"valid={row['valid']} try={attempt} ", end="")
        print(f"r{row['rep']} {row['ues']:>3} UEs x {row['rate_mbps']:g}M  off={row['offered_mbps']:g}  thr={row['throughput_mbps']}  "
              f"loss={row['loss_pct']}%  rtt={row['rtt_avg_ms']}/{row['rtt_p95_ms']}ms  upf1_cpu={row['upf1_cpu_pct']}% thr%={row['upf1_throttled_pct']}  "
              f"sess={row['upf1_sessions']}  upf_inside_loss={row['upf1_inside_loss_pct']}%  gnb={row['gnb_cores']} ue={row['ue_cores']} cores", flush=True)
        time.sleep(COOL)
        if row["valid"]:
            break


if __name__ == "__main__":
    main()
