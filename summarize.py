#!/usr/bin/env python3
"""Phase 7 table from repeated ramp.py runs.   usage: python3 summarize.py results/ramp_single_r* -- results/ramp_scaled_r*
Each argument is a ramp.py prefix; "--" separates the groups (first group = baseline). Steady state = from 10 s after the
last UE joined until 5 s before the end (or before the first UE leaves). Prints one row per run, then mean (min-max) per group, as Markdown.
  python3 summarize.py --down results/downscale/*_ues.csv     phase 8: one row per ramp-down run (when upf2 emptied, when it
                                                                was stopped, how long 2 UPFs ran for a load one UPF carries, cut UEs)"""
import csv, statistics, sys


def rows(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def run(prefix):
    tl, ues = rows(f"{prefix}_timeline.csv"), rows(f"{prefix}_ues.csv")
    last = max(num(u["start_s"]) for u in ues)
    end = min([max(num(r["sec"]) for r in tl)] + [num(u["left_s"]) for u in ues if num(u.get("left_s")) is not None])
    st = [r for r in tl if last + 10 <= num(r["sec"]) <= end - 5]
    def mean(k):
        v = [num(r[k]) for r in st if num(r[k]) is not None]
        return statistics.mean(v) if v else None
    def ue_loss(upf):
        v = [num(u["loss_pct"]) for u in ues if u["upf"] == upf and num(u["loss_pct"]) is not None]
        return statistics.mean(v) if v else None
    act = [r["action"] for r in tl if r.get("action")]
    up = next((num(r["sec"]) for r in tl if r.get("action", "").startswith("scale_up")), None)
    return {"offered": mean("offered_mbps"), "delivered": mean("delivered_mbps"), "loss": mean("loss_pct"), "rtt": mean("rtt_avg_ms"),
            "upf1_cpu": mean("upf1_cpu"), "upf2_cpu": mean("upf2_cpu"), "ues_upf2": sum(u["upf"] == "upf2" for u in ues),
            "loss_upf1_ues": ue_loss("upf1"), "loss_upf2_ues": ue_loss("upf2"), "scale_up_s": up,
            "failed_ues": sum(num(u.get("stopped_early")) == 1 or num(u["seconds_received"]) in (None, 0) for u in ues), "host_load": mean("host_load1"), "actions": len(act)}


COLS = ["offered", "delivered", "loss", "rtt", "upf1_cpu", "upf2_cpu", "ues_upf2", "loss_upf1_ues", "loss_upf2_ues", "scale_up_s", "failed_ues", "host_load"]


def fmt(v, d=1):
    return "-" if v is None else f"{v:.{d}f}"


ONE_UPF_MBPS = 30       # load at which upf1 reaches the scale-up limit (80% of cap): below it, one UPF is enough. --fits MBPS sets it
                        # from the current phase 3 sweep (30 on 2026-10-19, ~45 on 2026-11-10 with the faster CPU setting)


def down(prefix):
    tl, ues = rows(f"{prefix}_timeline.csv"), rows(f"{prefix}_ues.csv")
    t = lambda r: num(r["sec"])
    up = next((t(r) for r in tl if r.get("action", "").startswith("scale_up")), None)
    dn = next((r for r in tl if r.get("action", "").startswith("scale_down")), None)
    first_leave = min(num(u["left_s"]) for u in ues if num(u.get("left_s")) is not None)
    low = next((t(r) for r in tl if t(r) >= first_leave and num(r["offered_mbps"]) < ONE_UPF_MBPS), None)
    empty = next((t(r) for r in tl if up is not None and t(r) > up and num(r["upf2_sessions"]) == 0
                  and all(num(x["upf2_sessions"]) in (0, None) for x in tl if t(x) >= t(r))), None)
    two = [r for r in tl if low is not None and t(r) >= low and r["upfs"] == "2"]
    on2 = [u for u in ues if u["upf"] == "upf2"]
    cut = [u for u in ues if num(u.get("stopped_early")) == 1]
    dphase = [num(r["loss_pct"]) for r in tl if t(r) >= first_leave and num(r["loss_pct"]) is not None]
    return {"run": prefix.split("/")[-1], "scale_up_s": up, "ues_on_upf2": " ".join(u["n"] for u in on2) or "-",
            "first_leave_s": first_leave, "load_fits_one_s": low, "upf2_empty_s": empty,
            "scale_down_s": t(dn) if dn else None, "kind": (dn["action"].replace("scale_down", "").strip() or "graceful") if dn else "none",
            "2_upfs_after_low_s": (t(dn) - low) if dn and low is not None and t(dn) >= low else 0.0,
            "upf2_idle_s": (t(dn) - empty) if dn and empty is not None and t(dn) >= empty else 0.0,      # running with 0 sessions
            "upf2_cap_s_after_low": sum(max(num(r["upf2_cpu"]) or 0, 0) for r in two) / 100,
            "cut_ues": " ".join(u["n"] for u in cut) or "-",
            # a cut session comes back on upf1 with a new IP: outage = last second the old address delivered -> new address up
            "new_ip_s": next((t(r) for r in tl if dn and t(r) >= t(dn) and r.get("new_ue_ips")), None),
            "outage_s": next((t(r) - max(num(u["last_rx_s"]) for u in cut) for r in tl if cut and dn and t(r) >= t(dn) and r.get("new_ue_ips")), None),
            "loss_down_pct": statistics.mean(dphase) if dphase else None}


def main():
    global ONE_UPF_MBPS
    if "--fits" in sys.argv:
        i = sys.argv.index("--fits")
        ONE_UPF_MBPS = float(sys.argv[i + 1])
        del sys.argv[i:i + 2]
    if sys.argv[1:2] == ["--down"]:
        prefixes = list(dict.fromkeys(a.removesuffix("_ues.csv").removesuffix("_timeline.csv") for a in sys.argv[2:]))
        rs = [down(p) for p in prefixes]
        print("| " + " | ".join(rs[0]) + " |")
        print("|---" * len(rs[0]) + "|")
        for r in rs:
            print("| " + " | ".join(fmt(v) if isinstance(v, float) or v is None else str(v) for v in r.values()) + " |")
        groups = {}                                # variant name without _rN -> its runs
        for r in rs:
            groups.setdefault(r["run"].rsplit("_r", 1)[0], []).append(r)
        cols = ["scale_down_s", "upf2_idle_s", "2_upfs_after_low_s", "upf2_cap_s_after_low", "outage_s", "loss_down_pct"]
        print()
        print("| variant (n) | upf2 stopped at s | upf2 running empty, s | 2 UPFs after load fit on one, s | upf2 cap-seconds after that | outage of cut UE, s | loss in ramp-down % | how stopped |")
        print("|---|---|---|---|---|---|---|---|")
        for name, g in groups.items():
            def agg(k):
                v = [x[k] for x in g if isinstance(x[k], (int, float))]
                return f"{statistics.mean(v):.1f} ({min(v):.1f}-{max(v):.1f})" if v else "-"
            kinds = ", ".join(sorted({x["kind"] for x in g}))
            print(f"| {name} ({len(g)}) | " + " | ".join(agg(k) for k in cols) + f" | {kinds} |")
        return
    groups, cur = [], []
    for a in sys.argv[1:]:
        if a == "--":
            groups.append(cur)
            cur = []
        else:                    # a prefix, or any of its files (so a shell glob works)
            for suf in ("_timeline.csv", "_ues.csv", "_controller.csv"):
                a = a.removesuffix(suf)
            if a not in cur:
                cur.append(a)
    groups.append(cur)
    print("| run | " + " | ".join(COLS) + " |")
    print("|---" * (len(COLS) + 1) + "|")
    summary = []
    for g in groups:
        rs = [run(p) for p in g]
        for p, r in zip(g, rs):
            print(f"| {p.split('/')[-1]} | " + " | ".join(fmt(r[c]) for c in COLS) + " |")
        summary.append((g[0].split("/")[-1].rsplit("_r", 1)[0], rs))
    print()
    print("| group (n) | delivered Mbit/s | loss % | RTT ms | upf1 CPU % | UEs on upf2 | loss % UEs on upf1 | loss % UEs on upf2 | scale-up at s |")
    print("|---|---|---|---|---|---|---|---|---|")
    for name, rs in summary:
        def agg(k):
            v = [r[k] for r in rs if r[k] is not None]
            return f"{statistics.mean(v):.1f} ({min(v):.1f}-{max(v):.1f})" if v else "-"
        print(f"| {name} ({len(rs)}) | " + " | ".join(agg(k) for k in ("delivered", "loss", "rtt", "upf1_cpu", "ues_upf2", "loss_upf1_ues", "loss_upf2_ues", "scale_up_s")) + " |")


if __name__ == "__main__":
    main()
