#!/usr/bin/env python3
"""Phase 7 table from repeated ramp.py runs.   usage: python3 summarize.py results/ramp_single_r* -- results/ramp_scaled_r*
Each argument is a ramp.py prefix; "--" separates the groups (first group = baseline). Steady state = from 10 s after the
last UE joined until 5 s before the end. Prints one row per run, then mean (min-max) per group, as Markdown."""
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
    last, end = max(num(u["start_s"]) for u in ues), max(num(r["sec"]) for r in tl)
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


def main():
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
