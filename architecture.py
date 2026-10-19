#!/usr/bin/env python3
"""Draws architecture.png: containers, the N2/N3/N4/N6 links between them, and the scaling controller.
usage: python3 architecture.py        (needs matplotlib, like plot.py)"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

INK, MUTED, BG = "#1a1a19", "#6b6a63", "white"
N2, N3, N4, N6, OTHER, CTL = "#eb6834", "#2a78d6", "#1baf7a", "#d99500", "#9a998f", "#8e44ad"

fig, ax = plt.subplots(figsize=(15, 9))
ax.set_xlim(0, 100)
ax.set_ylim(-4, 62)
ax.axis("off")


def box(x0, y0, w, h, title, lines=(), color=INK, dashed=False, fill="#fafaf8"):
    ax.add_patch(FancyBboxPatch((x0, y0), w, h, boxstyle="round,pad=0.4,rounding_size=1.2", linewidth=2 if not dashed else 1.6,
                                edgecolor=color, facecolor=fill, linestyle=(0, (5, 3)) if dashed else "-", zorder=3))
    ax.text(x0 + w / 2, y0 + h - 2.2, title, ha="center", va="center", fontsize=12.5, fontweight="bold", color=INK, zorder=4)
    for i, l in enumerate(lines):
        ax.text(x0 + w / 2, y0 + h - 5.2 - 2.6 * i, l, ha="center", va="center", fontsize=9.3, color=MUTED, zorder=4)


def link(pts, color, label=None, at=0, dx=0.0, dy=1.3, dashed=False, ha="center", over=False):
    xs, ys = zip(*pts)
    kw = {"path_effects": [pe.Stroke(linewidth=7, foreground=BG), pe.Normal()]} if over else {}
    ax.plot(xs, ys, color=color, linewidth=2.4, linestyle=(0, (5, 3)) if dashed else "-", zorder=2, solid_capstyle="round", **kw)
    if label:
        (x1, y1), (x2, y2) = pts[at], pts[at + 1]
        ax.text((x1 + x2) / 2 + dx, (y1 + y2) / 2 + dy, label, ha=ha, va="bottom", fontsize=9, color=color, fontweight="bold",
                zorder=5, bbox=dict(facecolor=BG, edgecolor="none", pad=0.6))


# title
ax.text(1, 60.5, "Load-aware UPF scaling: test bed (Docker Compose, one host)", fontsize=16, fontweight="bold", color=INK, va="center")
ax.text(1, 57.8, "UE traffic goes UE → gNB → UPF → data network; the controller adds upf2 when upf1 is busy and removes it when idle",
        fontsize=10.5, color=MUTED, va="center")

# containers
box(1, 22, 14, 13, "ue", ["UERANSIM: N UEs", "uesimtun0..N-1", "10.10.1.31"])
box(22, 22, 14, 13, "gnb", ["UERANSIM gNB", "10.10.1.30"])
box(42, 40, 34, 14, "core  (control plane)", ["NRF · SCP · AMF · SMF · AUSF", "UDM · UDR · PCF · NSSF", "N2 10.10.1.10   N4 10.10.2.10"])
box(82, 42, 15, 10, "mongo", ["60 subscribers"])
box(52, 22, 17, 13, "upf1", ["user plane", "N3 .1.20  N4 .2.20  N6 .3.20", "cap 0.1 core"], color=N3, fill="#eef4fc")
box(52, 1, 17, 13, "upf2", ["profile: scale", "N3 .1.21  N4 .2.21  N6 .3.21", "cap 0.1 core"], color=N3, dashed=True)
box(82, 11, 15, 13, "dn", ["data network", "iperf3 + ping", "10.10.3.30"])
box(1, -3, 37, 15, "controller.py  (runs on the host)", ["every 1 s: each UPF's CPU + throttling (Docker API)",
                                                       "upf1 CPU ≥ 80% of cap for 5 s → start upf2",
                                                       "low load and upf2 has 0 sessions → stop upf2"], color=CTL, fill="#f6effa")

# links
link([(15.4, 28.5), (21.6, 28.5)], OTHER, "radio", dy=0.7)
ax.text(18.5, 27.6, "UDP\n4997", ha="center", va="top", fontsize=8.5, color=OTHER, fontweight="bold", zorder=5)
link([(29, 35.4), (29, 47), (41.6, 47)], N2, "N2 · NGAP / SCTP 38412", at=1, dy=1.0)
link([(66, 39.6), (66, 35.4)], N4, "N4 · PFCP 8805", dx=1.2, dy=0.2, ha="left")
link([(45, 39.6), (45, 16), (60.5, 16), (60.5, 14.4)], N4, "N4 · PFCP", at=1, dx=-3.5, dy=0.4, dashed=True)
link([(36.4, 30), (51.6, 30)], N3, "N3 · GTP-U 2152", dy=0.9, over=True)
link([(36.4, 25), (40, 25), (40, 7), (51.6, 7)], N3, "N3 · GTP-U", at=2, dx=1.5, dy=0.6, dashed=True)
link([(69.4, 28.5), (75, 28.5), (75, 21), (81.6, 21)], N6, "N6 · IP (NAT)", at=0, dx=-1.6, dy=0.8, ha="left")
link([(69.4, 7), (89.5, 7), (89.5, 10.6)], N6, "N6", at=0, dx=4, dy=0.6, dashed=True)
link([(76.4, 47), (81.6, 47)], OTHER, "db", dy=0.6)
# the UPFs the controller manages, and its one link to them
ax.add_patch(FancyBboxPatch((49.6, -1.6), 22.2, 38.6, boxstyle="round,pad=0.3,rounding_size=1.5", linewidth=1.6, edgecolor=CTL,
                            facecolor="none", linestyle=(0, (1.5, 2.5)), zorder=1))
ax.text(60.7, -0.4, "UPFs managed by the controller", ha="center", va="center", fontsize=9, color=CTL, fontweight="bold", zorder=4)
ax.annotate("", xy=(49.4, 3.5), xytext=(38.6, 3.5), arrowprops=dict(arrowstyle="-|>", color=CTL, lw=2.2, linestyle="--"), zorder=5)
ax.text(44, 2.6, "reads CPU,\nstarts / stops upf2", ha="center", va="top", fontsize=9, color=CTL, fontweight="bold", zorder=5)

# legend: what each network carries
items = [(N2, "N2 + N3 share Docker network n2n3 (10.10.1.0/24)"), (N4, "N4: network n4 (10.10.2.0/24), SMF ↔ UPFs"),
         (N6, "N6: network n6 (10.10.3.0/24), UPFs ↔ dn"), (N3, "dashed = upf2, only running after scale-up")]
for i, (c, t) in enumerate(items):
    y = 35 - 3.0 * i
    ax.plot([80.5, 83.5], [y, y], color=c, linewidth=2.6, linestyle=(0, (5, 3)) if i == 3 else "-")
    ax.text(84.3, y, t, fontsize=8.6, color=INK, va="center")

fig.savefig("architecture.png", dpi=150, bbox_inches="tight", facecolor=BG)
print("architecture.png")
