#!/usr/bin/env python3
"""Draws architecture.png: the containers and their N2/N3/N4/N6 links (top), and the controller's loop (bottom).
usage: PYTHONNOUSERSITE=1 python3 architecture.py        (needs matplotlib, like plot.py)"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

INK, MUTED, BG, RULE = "#1a1a19", "#6b6a63", "white", "#e4e3dd"
N2, N3, N4, N6, OTHER, CTL = "#eb6834", "#2a78d6", "#1baf7a", "#d99500", "#9a998f", "#8e44ad"
UP, DOWN, CUT = "#2a78d6", "#1baf7a", "#d64545"
DASH = (0, (5, 3))

fig, ax = plt.subplots(figsize=(15, 19))
ax.set_xlim(0, 100)
ax.set_ylim(-68, 63)
ax.axis("off")


def box(x0, y0, w, h, title, lines=(), color=INK, dashed=False, fill="#fafaf8", size=12.5):
    ax.add_patch(FancyBboxPatch((x0, y0), w, h, boxstyle="round,pad=0.4,rounding_size=1.2", linewidth=1.6 if dashed else 2,
                                edgecolor=color, facecolor=fill, linestyle=DASH if dashed else "-", zorder=3))
    top = y0 + h / 2 + 1.25 * len(lines)          # title + lines centred vertically as a block
    ax.text(x0 + w / 2, top, title, ha="center", va="center", fontsize=size, fontweight="bold", color=INK, zorder=4)
    for i, l in enumerate(lines):
        ax.text(x0 + w / 2, top - 2.7 * (i + 1), l, ha="center", va="center", fontsize=9.2, color=MUTED, zorder=4)


def line(pts, color, dashed=False, lw=2.4):
    xs, ys = zip(*pts)
    ax.plot(xs, ys, color=color, linewidth=lw, linestyle=DASH if dashed else "-", zorder=2, solid_capstyle="round")


def label(x, y, text, color, ha="center", size=9, weight="bold"):
    ax.text(x, y, text, ha=ha, va="center", fontsize=size, color=color, fontweight=weight, zorder=6,
            bbox=dict(facecolor=BG, edgecolor="none", pad=0.6))


def arrow(p1, p2, color, dashed=False, lw=2.2):
    ax.annotate("", xy=p2, xytext=p1, zorder=5, arrowprops=dict(arrowstyle="-|>", color=color, lw=lw, mutation_scale=16,
                linestyle="--" if dashed else "-", shrinkA=0, shrinkB=0))


# ================= test bed =================
ax.text(1, 61.5, "Load-aware UPF scaling: test bed", fontsize=17, fontweight="bold", color=INK, va="center")
ax.text(1, 58.8, "Open5GS 2.8.0 + UERANSIM 3.2.6 in Docker Compose on one host. User traffic: UE → gNB → UPF → data network. "
        "Dashed = upf2, running only after scale-up.", fontsize=10.3, color=MUTED, va="center")

box(1, 24, 12, 12, "ue", ["UERANSIM", "N UEs", "10.10.1.31"])
box(19, 24, 12, 12, "gnb", ["UERANSIM gNB", "10.10.1.30"])
box(39, 22.5, 24, 15, "core", ["control plane: AMF · SMF", "NRF · SCP · AUSF · UDM", "UDR · PCF · NSSF",
                               "N2 .1.10   N4 .2.10"])
box(68, 26, 10, 8, "mongo", ["subscribers"], size=11)
box(84, 24, 14, 12, "dn", ["data network", "iperf3 + ping", "10.10.3.30"])
box(39, 44, 24, 11, "upf1", ["N3 .1.20 · N4 .2.20 · N6 .3.20", "cap 0.1 core"], color=N3, fill="#eef4fc")
box(39, 5, 24, 11, "upf2", ["N3 .1.21 · N4 .2.21 · N6 .3.21", "cap 0.1 core · profile scale"], color=N3, dashed=True)

# links (no crossings: upf1 above the core, upf2 below it)
line([(13.4, 30), (18.6, 30)], OTHER)
label(16, 32.2, "radio", OTHER, size=8.5)
line([(31.4, 30), (38.6, 30)], N2)
label(35, 32.4, "N2", N2)
label(35, 27.6, "NGAP", N2, size=8, weight="normal")
line([(25, 36.4), (25, 49.5), (38.6, 49.5)], N3)
label(31.8, 51.7, "N3 · GTP-U 2152", N3)
line([(25, 23.6), (25, 10.5), (38.6, 10.5)], N3, dashed=True)
label(31.8, 12.7, "N3 · GTP-U", N3)
line([(51, 37.9), (51, 43.6)], N4)
label(52, 40.7, "N4 · PFCP 8805", N4, ha="left")
line([(51, 22.1), (51, 16.4)], N4, dashed=True)
label(52, 19.3, "N4 · PFCP", N4, ha="left")
line([(63.4, 49.5), (91, 49.5), (91, 36.4)], N6)
label(77, 51.7, "N6 · IP, NAT to the UPF's address", N6)
line([(63.4, 10.5), (91, 10.5), (91, 23.6)], N6, dashed=True)
label(77, 12.7, "N6 · IP (NAT)", N6)
line([(63.4, 30), (67.6, 30)], OTHER)

# controller, under upf2
box(33, -10, 36, 9.5, "controller.py  (on the host)", ["reads both UPFs' CPU, throttling and sessions every 1 s;",
                                                      "starts / stops upf2 with docker compose"], color=CTL, fill="#f6effa", size=12)
arrow((51, -0.1), (51, 4.4), CTL, dashed=True)

# networks
for i, (c, t) in enumerate([(N2, "n2n3 10.10.1.0/24: N2 (gNB ↔ AMF) and N3 (gNB ↔ UPFs)"),
                            (N4, "n4 10.10.2.0/24: SMF ↔ UPFs"), (N6, "n6 10.10.3.0/24: UPFs ↔ dn")]):
    y = 4 - 3 * i
    ax.plot([1.5, 4.5], [y, y], color=c, linewidth=2.6)
    ax.text(5.3, y, t, fontsize=8.8, color=INK, va="center")
ax.text(1.5, -6, "UE pool 10.45.0.0/16, shared by both UPFs", fontsize=8.8, color=MUTED, va="center")

# ================= controller loop =================
ax.plot([1, 99], [-14, -14], color=RULE, linewidth=1.2)
ax.text(1, -17, "Controller loop  (controller.py, once per second)", fontsize=15, fontweight="bold", color=INK, va="center")
ax.text(1, -19.8, "Thresholds are % of one UPF's 0.1-core cap (from the phase 3 sweep); every decision is logged to a CSV.",
        fontsize=10.3, color=MUTED, va="center")

steps = [("1  Sense", ["CPU + throttled periods", "(Docker API), sessions", "(UPF metrics)"]),
         ("2  Smooth", ["3 s average,", "CPU as % of cap"]),
         ("3  Decide", ["thresholds, hold timers,", "30 s cooldown"]),
         ("4  Act + log", ["docker compose", "up / stop upf2"])]
for i, (t, ls) in enumerate(steps):
    x = 3 + i * 24.5
    box(x, -34, 19, 10.5, t, ls, color=CTL, fill="#f6effa", size=12)
    if i < 3:
        arrow((x + 19.5, -28.75), (x + 24.1, -28.75), CTL)
line([(85, -34.5), (85, -37.5), (12.5, -37.5)], CTL, lw=1.6)
arrow((12.5, -37.5), (12.5, -34.5), CTL, lw=1.6)
label(48.7, -37.5, "next second", CTL, size=8.5, weight="normal")

box(3, -54, 19, 9, "1 UPF", ["upf1 only"])
box(40.5, -54, 19, 9, "2 UPFs", ["new sessions round-robin"], color=UP, fill="#eef4fc")
box(78, -54, 19, 9, "draining", ["load low, upf2 still", "holds sessions"], color=DOWN, fill="#eefaf4")
arrow((22.5, -47.5), (40, -47.5), UP)
label(31.2, -45.3, "upf1 ≥ 80% of cap for 5 s", UP)
label(31.2, -49.8, "start upf2; usable 3–7 s later", UP, size=8, weight="normal")
arrow((60, -47.5), (77.5, -47.5), DOWN)
label(68.7, -45.3, "total CPU < 35% for 20 s", DOWN)
arrow((77.5, -51.8), (60, -51.8), MUTED, lw=1.5)
label(68.7, -51.8, "load rises again", MUTED, size=8, weight="normal")
line([(87.5, -54.4), (87.5, -57.5), (12.5, -57.5)], DOWN)
arrow((12.5, -57.5), (12.5, -54.4), DOWN)
label(50, -57.5, "graceful: upf2 has 0 sessions → stop it (no traffic lost)", DOWN)
line([(92.5, -54.4), (92.5, -61.5), (7.5, -61.5)], CUT, dashed=True)
arrow((7.5, -61.5), (7.5, -54.4), CUT, dashed=True)
label(50, -61.5, "forced (--drain-timeout): stop it anyway → its UEs lose ~11 s and come back on upf1 with a new IP", CUT)
line([(50, -44.6), (50, -41.2), (12.5, -41.2)], DOWN, dashed=True, lw=1.8)
arrow((12.5, -41.2), (12.5, -44.6), DOWN, dashed=True, lw=1.8)
label(31.2, -41.2, "--empty-down: upf2 empty 20 s, upf1 < 80% → stop upf2", DOWN, size=8.5)
ax.text(1, -65, "Open5GS cannot stop placing new sessions on a UPF, or move a live one, so a graceful drain must wait for "
        "upf2's UEs to leave.", fontsize=9, color=MUTED, va="center", style="italic")

fig.savefig("architecture.png", dpi=150, bbox_inches="tight", facecolor=BG)
print("architecture.png")
