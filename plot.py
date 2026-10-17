#!/usr/bin/env python3
"""CSVs -> graphs (needs matplotlib: pip3 install matplotlib).
  python3 plot.py sweep results/a.csv [results/b.csv ...]     load sweep: CPU, throttling, loss vs offered load (median, min-max band), one line per file
  python3 plot.py ramp results/single results/scaled          ramp runs (ramp.py prefixes): timeline of load, loss, RTT, CPU, sessions, one colour per run
The PNG is written next to the first input."""
import csv, os, statistics, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]     # fixed order: first input is always blue
INK, MUTED, GRID = "#1a1a19", "#6b6a63", "#e4e3dd"
plt.rcParams.update({"axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": MUTED, "ytick.color": MUTED, "axes.grid": True,
                     "grid.color": GRID, "grid.linewidth": 0.6, "axes.spines.top": False, "axes.spines.right": False,
                     "font.size": 9, "axes.titlesize": 10, "axes.titleweight": "bold", "legend.frameon": False, "lines.linewidth": 2})


def rows(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def num(r, k):
    try:
        return float(r[k])
    except (KeyError, TypeError, ValueError):
        return None


def label(path):
    return os.path.basename(path).removesuffix(".csv")


def sweep(paths):
    panels = [("upf1_cpu_pct", "UPF CPU (% of cap)"), ("upf1_throttled_pct", "Throttled 10 ms periods (%)"), ("loss_pct", "Loss (%)"),
              ("rtt_p95_ms", "RTT p95 (ms)")]
    fig, axs = plt.subplots(1, len(panels), figsize=(3.2 * len(panels), 3), constrained_layout=True)
    for i, p in enumerate(paths):
        rs = [r for r in rows(p) if r.get("valid", "1") == "1"]
        loads = sorted({num(r, "offered_mbps") for r in rs})
        for ax, (k, title) in zip(axs, panels):
            vals = [[v for r in rs if num(r, "offered_mbps") == x and (v := num(r, k)) is not None] for x in loads]
            xs = [x for x, v in zip(loads, vals) if v]
            vals = [v for v in vals if v]
            ax.fill_between(xs, [min(v) for v in vals], [max(v) for v in vals], color=COLORS[i], alpha=0.15, linewidth=0)
            ax.plot(xs, [statistics.median(v) for v in vals], color=COLORS[i], marker="o", markersize=4, label=label(p))
            ax.set_title(title, loc="left")
            ax.set_xlabel("Offered load (Mbit/s)")
    for ax in axs:
        ax.set_ylim(bottom=0)
    if len(paths) > 1:
        axs[0].legend(loc="upper left")
    return fig


def ramp(prefixes):
    panels = ["Load (Mbit/s)", "Loss (%)", "RTT (ms)", "CPU (% of one UPF's cap)", "Sessions"]
    fig, axs = plt.subplots(len(panels), 1, figsize=(7.5, 10), sharex=True, constrained_layout=True)
    for i, p in enumerate(prefixes):
        rs, c, name = rows(f"{p}_timeline.csv"), COLORS[i], os.path.basename(p)
        t = [num(r, "sec") for r in rs]
        def series(k):
            return [num(r, k) for r in rs]
        if i == 0:
            axs[0].step(t, series("offered_mbps"), where="post", color=MUTED, linewidth=1, linestyle=":", label="offered")
        axs[0].plot(t, series("delivered_mbps"), color=c, label=f"{name} delivered")
        axs[1].plot(t, series("loss_pct"), color=c, label=name)
        axs[2].plot(t, series("rtt_avg_ms"), color=c, label=name)
        for ax, k in ((axs[3], "cpu"), (axs[4], "sessions")):
            for u, ls in (("upf1", "-"), ("upf2", "--")):
                s = series(f"{u}_{k}")
                if any(v is not None for v in s):
                    ax.plot(t, s, color=c, linestyle=ls, label=f"{name} {u}")
        for r in rs:
            if r.get("action"):
                for ax in axs:
                    ax.axvline(num(r, "sec"), color=c, linewidth=1, alpha=0.6)
                axs[0].annotate(r["action"].split()[0], (num(r, "sec"), axs[0].get_ylim()[1]), color=INK, fontsize=8,
                                xytext=(3, -12), textcoords="offset points")
    for ax, title in zip(axs, panels):
        ax.set_title(title, loc="left")
        ax.set_ylim(bottom=0)
        ax.legend(loc="upper left", fontsize=8)
    axs[-1].set_xlabel("Time since start (s)")
    return fig


def main():
    kind, inputs = sys.argv[1], sys.argv[2:]
    fig = {"sweep": sweep, "ramp": ramp}[kind](inputs)
    base = inputs[0].removesuffix(".csv")
    out = f"{base}_{kind}.png" if kind == "ramp" or len(inputs) > 1 else f"{base}.png"
    fig.savefig(out, dpi=150)
    print(out)


if __name__ == "__main__":
    main()
