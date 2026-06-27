#!/usr/bin/env python3
"""Generate clean architecture diagrams (PNG) for the developer docs."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib.lines import Line2D

BLUE = "#1F4E79"; MID = "#2E5A88"; LIGHT = "#D9E7F3"; CARD = "#F4F8FC"
GREEN = "#2E7D5B"; AMBER = "#B26B00"; GREY = "#6B7785"; INK = "#1A2430"
EDGE = "#9BB4CC"

plt.rcParams["font.family"] = "DejaVu Sans"


def box(ax, x, y, w, h, title, sub=None, fill=CARD, edge=BLUE, tcol=INK, fs=11, lw=1.6):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08",
                                linewidth=lw, edgecolor=edge, facecolor=fill, zorder=2))
    if sub:
        ax.text(x + w / 2, y + h * 0.62, title, ha="center", va="center", fontsize=fs,
                fontweight="bold", color=tcol, zorder=3)
        ax.text(x + w / 2, y + h * 0.28, sub, ha="center", va="center", fontsize=fs - 2.5,
                color=GREY, zorder=3)
    else:
        ax.text(x + w / 2, y + h / 2, title, ha="center", va="center", fontsize=fs,
                fontweight="bold", color=tcol, zorder=3)


def arrow(ax, p1, p2, color=MID, style="-|>", lw=1.8, ls="-", rad=0.0):
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle=style, mutation_scale=14,
                                 linewidth=lw, color=color, linestyle=ls,
                                 connectionstyle=f"arc3,rad={rad}", zorder=1))


def label(ax, x, y, text, color=GREY, fs=8.5, ha="center", style="italic"):
    ax.text(x, y, text, ha=ha, va="center", fontsize=fs, color=color, fontstyle=style, zorder=4)


def setup(w=12, h=7):
    fig, ax = plt.subplots(figsize=(w, h))
    ax.set_xlim(0, 100); ax.set_ylim(0, 100 * h / w)
    ax.axis("off")
    return fig, ax


def save(fig, name):
    fig.savefig(f"/home/claude/diagrams/{name}", dpi=150, bbox_inches="tight",
                facecolor="white", pad_inches=0.15)
    plt.close(fig)
    print("wrote", name)


# ---------- 1. System architecture ----------
fig, ax = setup(12, 6.6)
H = 55
ax.text(50, 52, "System Architecture", ha="center", fontsize=15, fontweight="bold", color=BLUE)
box(ax, 4, 32, 17, 11, "Browser", "dashboard.html (SPA)", fill=LIGHT)
# host boundary
ax.add_patch(FancyBboxPatch((26, 4), 70, 44, boxstyle="round,pad=0.2,rounding_size=0.4",
             linewidth=1.4, edgecolor=EDGE, facecolor="#FBFCFE", linestyle="--", zorder=0))
ax.text(28.5, 45.5, "Docker host (AlmaLinux)", fontsize=9, color=GREY, fontweight="bold")
box(ax, 30, 32, 17, 11, "web", "Node :8080", fill="#E7F0FA")
box(ax, 56, 38, 17, 9, "app", "Python API", fill="#E7F0FA")
box(ax, 56, 26, 17, 9, "workers", "1 proc/inverter", fill="#EAF5EF", edge=GREEN)
box(ax, 80, 40, 14, 7.5, "redis", "live bus", fill=CARD)
box(ax, 80, 30, 14, 7.5, "config-db", "MariaDB", fill=CARD)
box(ax, 80, 20, 14, 7.5, "data-db", "MongoDB", fill=CARD)
box(ax, 30, 12, 17, 9, "inverter", "GoodWe GW6000ES", fill="#FBF1E2", edge=AMBER)
# arrows
arrow(ax, (21, 37.5), (30, 37.5)); label(ax, 25.5, 40, "HTTP / SSE")
arrow(ax, (47, 37), (56, 41), rad=-0.1); label(ax, 51, 43.5, "proxy /api")
arrow(ax, (47, 35.5), (80, 43), color=GREEN, rad=-0.15); label(ax, 64, 47, "SSE / data", color=GREEN)
arrow(ax, (64.5, 38), (64.5, 35), color=GREY, style="<|-|>"); label(ax, 70, 36.5, "supervise")
arrow(ax, (73, 41), (80, 41.5)); label(ax, 76, 39.5, "pub/sub", fs=7.5)
arrow(ax, (73, 40), (80, 32), rad=0.1)
arrow(ax, (64.5, 26), (64.5, 24)); arrow(ax, (73, 28), (80, 24), color=GREEN, rad=-0.1)
label(ax, 78, 26, "writes", color=GREEN, fs=7.5)
arrow(ax, (47, 30), (47, 21), color=AMBER, style="<|-|>", rad=0.0)
label(ax, 52.5, 25, "UDP 8899 /\nTCP 502", color=AMBER, fs=8)
arrow(ax, (39, 31.5), (39, 21.5), color=AMBER, style="<|-|>")
save(fig, "arch-system.png")

# ---------- 2. Live data pipeline ----------
fig, ax = setup(12, 4.4)
ax.text(50, 33, "Live Data Pipeline", ha="center", fontsize=15, fontweight="bold", color=BLUE)
y = 14
box(ax, 1, y, 15, 10, "Inverter", "Modbus/UDP", fill="#FBF1E2", edge=AMBER)
box(ax, 20, y, 15, 10, "worker", "poll + decode", fill="#EAF5EF", edge=GREEN)
box(ax, 39, y + 6, 16, 9, "data-db", "store reading", fill=CARD)
box(ax, 39, y - 6, 16, 9, "redis", "publish", fill=CARD)
box(ax, 60, y, 14, 10, "web", "SSE stream", fill="#E7F0FA")
box(ax, 79, y, 18, 10, "Browser", "apply() → UI", fill=LIGHT)
arrow(ax, (16, y + 5), (20, y + 5), color=AMBER); label(ax, 18, y + 7.5, "read", fs=8)
arrow(ax, (35, y + 6), (39, y + 9), color=GREEN, rad=-0.1)
arrow(ax, (35, y + 4), (39, y - 1.5), color=GREEN, rad=0.1)
arrow(ax, (55, y - 1.5), (60, y + 4), rad=0.1); label(ax, 58, y - 3, "subscribe", fs=7.5)
arrow(ax, (74, y + 5), (79, y + 5)); label(ax, 76.5, y + 7.5, "SSE", fs=8)
label(ax, 47, y + 11.5, "history", color=GREY, fs=8)
save(fig, "arch-dataflow.png")

# ---------- 3. App process model ----------
fig, ax = setup(11, 5.2)
ax.text(50, 42, "app Container — Process & Thread Model", ha="center", fontsize=14, fontweight="bold", color=BLUE)
ax.add_patch(FancyBboxPatch((3, 4), 94, 34, boxstyle="round,pad=0.2,rounding_size=0.4",
             linewidth=1.3, edgecolor=EDGE, facecolor="#FBFCFE", zorder=0))
box(ax, 7, 24, 24, 9, "HTTP API server", "do_GET / do_POST", fill="#E7F0FA")
box(ax, 7, 10, 24, 9, "Supervisor", "spawns/monitors", fill=CARD)
box(ax, 38, 26, 25, 8, "mirror thread", "redis ← snapshots", fill=CARD)
box(ax, 38, 14, 17, 8, "worker 1", "inverter A", fill="#EAF5EF", edge=GREEN)
box(ax, 58, 14, 17, 8, "worker 2", "inverter B", fill="#EAF5EF", edge=GREEN)
box(ax, 78, 14, 15, 8, "worker N", "…", fill="#EAF5EF", edge=GREEN)
arrow(ax, (19, 24), (19, 19), color=GREY)
arrow(ax, (31, 14.5), (38, 17.5), color=GREEN, rad=-0.1)
arrow(ax, (31, 13), (58, 15), color=GREEN, rad=-0.05)
arrow(ax, (31, 12), (78, 15), color=GREEN, rad=-0.05)
arrow(ax, (46, 22), (46, 26), color=MID); label(ax, 52, 24, "queue", fs=8)
ax.text(50, 6.5, "Each worker is an isolated OS process; a crash in one never affects the others.",
        ha="center", fontsize=8.5, color=GREY, fontstyle="italic")
save(fig, "arch-process.png")

# ---------- 4. Inverter connection decision ----------
fig, ax = setup(10, 5.6)
ax.text(50, 47, "Inverter Connection Logic", ha="center", fontsize=14, fontweight="bold", color=BLUE)
box(ax, 36, 38, 28, 7, "scan / connect", fill="#E7F0FA")
box(ax, 8, 24, 26, 8, "TCP 502 open?", fill=CARD)
box(ax, 66, 24, 26, 8, "UDP 8899 reply?", fill=CARD)
box(ax, 4, 8, 30, 8, "Modbus-TCP driver", "newer dongle", fill="#EAF5EF", edge=GREEN)
box(ax, 66, 8, 30, 8, "goodwe UDP driver", "family ES/ET/…", fill="#EAF5EF", edge=GREEN)
box(ax, 38, 8, 22, 8, "fix network /\ncheck IP", fill="#FBE9E7", edge=AMBER)
arrow(ax, (44, 38), (24, 32), rad=0.15)
arrow(ax, (56, 38), (78, 32), rad=-0.15)
arrow(ax, (20, 24), (19, 16), color=GREEN); label(ax, 14, 20, "yes", color=GREEN, fs=8.5)
arrow(ax, (79, 24), (80, 16), color=GREEN); label(ax, 85, 20, "yes", color=GREEN, fs=8.5)
arrow(ax, (28, 24), (45, 16), color=AMBER, rad=-0.1); label(ax, 33, 21, "no", color=AMBER, fs=8.5)
arrow(ax, (72, 24), (54, 16), color=AMBER, rad=0.1); label(ax, 67, 21, "no", color=AMBER, fs=8.5)
ax.text(50, 2.5, "This deployment: TCP 502 open  →  Modbus-TCP driver.",
        ha="center", fontsize=9, color=BLUE, fontweight="bold")
save(fig, "arch-connect.png")

# ---------- 5. Deploy / update flow ----------
fig, ax = setup(12, 3.8)
ax.text(50, 30, "Build & Self-Update Flow", ha="center", fontsize=14, fontweight="bold", color=BLUE)
y = 12
box(ax, 1, y, 16, 9, "Settings →\nUpdate now", fill=LIGHT)
box(ax, 21, y, 15, 9, "app", "write trigger", fill="#E7F0FA")
box(ax, 40, y, 17, 9, ".update/request", "(bind mount)", fill=CARD)
box(ax, 61, y, 16, 9, "systemd .path", "watcher", fill=CARD)
box(ax, 81, y, 17, 9, "update.sh", "pull+build+up", fill="#EAF5EF", edge=GREEN)
for a, b in [((17, y + 4.5), (21, y + 4.5)), ((36, y + 4.5), (40, y + 4.5)),
             ((57, y + 4.5), (61, y + 4.5)), ((77, y + 4.5), (81, y + 4.5))]:
    arrow(ax, a, b)
arrow(ax, (89, y), (89, y - 5), color=GREEN)
arrow(ax, (89, y - 5), (9, y - 5), color=GREEN, rad=0.0)
arrow(ax, (9, y - 5), (9, y), color=GREEN)
label(ax, 49, y - 3.2, "restart stack with new commit baked in", color=GREEN, fs=8.5)
save(fig, "arch-deploy.png")

print("all diagrams done")
