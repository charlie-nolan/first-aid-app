"""Right card by voice turn, old one-question-per-page flow vs the fill-in-the-blanks form.
Numbers: PC sim at engine b4d9b2c, slots v15, single tree (tree known), 2026-11-17."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

SETS = {  # set: (turn 1, old turn 2, form turn 2)
    "dev (666)": (69.8, 84.2, 89.7), "dev2 (120)": (56.9, 64.4, 64.4), "dev3 (240)": (88.7, 89.4, 89.4),
    "test2 (612)": (43.0, 63.3, 76.3), "test3 (678)": (39.7, 67.1, 77.4)}
fig, ax = plt.subplots(figsize=(9, 5), dpi=150)
names = list(SETS)
x = range(len(names))
w = 0.27
cols = ("#9aa5b1", "#5b8def", "#1f9d55")
labels = ("turn 1 (first sentence)", "turn 2, one question per page", "turn 2, fill-in-the-blanks form")
ax.axhspan(70, 80, color="#fff3c4", zorder=0, label="Charlie's turn-2 target 70-80%")
for k in range(3):
    vals = [SETS[n][k] for n in names]
    bars = ax.bar([i + (k - 1) * w for i in x], vals, w, label=labels[k], color=cols[k], zorder=2)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 1, f"{v:.0f}", ha="center", fontsize=8)
ax.set_xticks(list(x), names)
ax.set_ylim(0, 100)
ax.set_ylabel("right card (%)")
ax.set_title("Right card by turn: TYPED-TEXT SIMULATION, right tree given (no router, no speech)\nengine b4d9b2c, slots v15, 2026-11-17. Superseded by later engine changes; not measured with real voice or on the device.", fontsize=9)
ax.legend(fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=2, frameon=False)
ax.spines[["top", "right"]].set_visible(False)
fig.tight_layout()
out = Path(__file__).parent / "2026-11-17_turn-accuracy_run-b4d9b2c_test-1.png"
fig.savefig(out)
print(out)
