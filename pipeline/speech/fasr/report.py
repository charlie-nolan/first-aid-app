"""README-style visuals for the PRs: training curves, a run-vs-run accuracy chart and a results
table (the things well-documented ML repos show), drawn with Pillow only (no matplotlib on the S3
toolchain box).

    python -m fasr report --runs firstaid firstaid2 m --set me_dev/eval

Writes into pipeline/speech/docs/runs/ (same naming as fasr snap):
    <date>_curves_<run>_testNN.png      loss + dev phone error vs step, one per run (from runs/<run>/log.txt)
    <date>_compare_<runs>_testNN.png    grouped bars: keyword recall, keyword in 5-best, wrong keywords/clip
    <date>_table_<runs>_testNN.png      the numbers as a table (+ RESULTS.md, appended)
Metrics only: nothing from the manuals or the recordings' wording.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

from .snap import OUT, _font, _name

BG, FG, DIM, GRID = (255, 255, 255), (30, 32, 38), (110, 115, 125), (225, 228, 233)
COLORS = [(46, 110, 200), (230, 126, 34), (39, 174, 96), (155, 89, 182), (192, 57, 43)]
METRICS = [("slot_recall", "keywords caught (top guess)", True), ("exact_in_5best", "whole sentence in 5-best", True),
           ("slot_false_per_clip", "wrong keywords per clip", False), ("wer", "word error", False)]


def work():
    from .evaluate import WORK
    return WORK


def parse_log(p: Path):
    loss, per = [], []
    step = 0
    for l in p.read_text(errors="replace").splitlines():
        m = re.search(r"step (\d+) loss ([\d.]+)", l)
        if m:
            step = int(m.group(1)); loss.append((step, float(m.group(2))))
        m = re.search(r"dev phone error ([\d.]+)", l)
        if m:
            per.append((step, float(m.group(1))))
    return loss, per


def _axes(d, box, xs, ys, title, ylab, f, fs):
    x0, y0, x1, y1 = box
    d.text((x0, y0 - 28), title, font=f, fill=FG)
    lo, hi = min(ys), max(ys)
    if hi - lo < 1e-9:
        hi = lo + 1
    for i in range(5):
        y = y1 - (y1 - y0) * i / 4
        d.line((x0, y, x1, y), fill=GRID)
        d.text((x0 - 52, y - 8), f"{lo + (hi - lo) * i / 4:.2f}", font=fs, fill=DIM)
    d.text((x0, y1 + 8), f"step 0 … {max(xs):,}", font=fs, fill=DIM)
    d.text((x1 - 90, y1 + 8), ylab, font=fs, fill=DIM)
    return lambda x, y: (x0 + (x1 - x0) * x / max(1, max(xs)), y1 - (y1 - y0) * (y - lo) / (hi - lo))


def curves(run: str) -> Path | None:
    from PIL import Image, ImageDraw
    log = work() / "speech" / "runs" / run / "log.txt"
    if not log.exists():
        print("no log:", log); return None
    loss, per = parse_log(log)
    if not loss:
        print("no steps in", log); return None
    f, fs = _font(18), _font(13)
    im = Image.new("RGB", (1200, 460), BG); d = ImageDraw.Draw(im)
    d.text((24, 12), f"Training curves: run {run}", font=_font(22), fill=FG)
    for k, (pts, title, lab) in enumerate(((loss, "CTC loss (lower is better)", "loss"),
                                          (per, "dev phone error (lower is better)", "PER"))):
        if not pts:
            continue
        box = (90 + k * 580, 90, 540 + k * 580, 400)
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        tr = _axes(d, box, xs, ys, title, lab, f, fs)
        d.line([tr(x, y) for x, y in pts], fill=COLORS[k], width=2)
        d.text((box[2] - 140, box[1] + 4), f"last {ys[-1]:.3f}", font=fs, fill=COLORS[k])
    path = _name("curves", run, dt.date.today().isoformat()); im.save(path); return path


def load_eval(run: str, key: str):
    best = None
    for p in sorted((work() / "speech" / "runs" / run).glob("eval*.json")):
        rep = json.loads(p.read_text())
        for sp, res in rep.items():
            if key in sp and isinstance(res, dict):
                for cond, m in res.items():
                    if isinstance(m, dict) and "wer" in m and (best is None or cond == "clean"):
                        best = m
    return best


def compare(runs, key) -> tuple[Path, Path, str]:
    from PIL import Image, ImageDraw
    rows = {r: load_eval(r, key) for r in runs}
    rows = {r: m for r, m in rows.items() if m}
    tag = "-".join(rows) or "none"
    f, fs, fb = _font(16), _font(13), _font(22)
    W, H = 1200, 480
    im = Image.new("RGB", (W, H), BG); d = ImageDraw.Draw(im)
    d.text((24, 12), f"Run comparison on {key}", font=fb, fill=FG)
    gx, gw = 70, (W - 100) // len(METRICS)
    bw = max(14, (gw - 60) // max(1, len(rows)))
    for gi, (k, label, up) in enumerate(METRICS):
        vals = [rows[r].get(k) or 0 for r in rows]
        hi = max(1.0, max(vals) * 1.15) if k == "slot_false_per_clip" else 1.0
        x = gx + gi * gw
        d.line((x, 400, x + gw - 30, 400), fill=GRID)
        for ri, (r, v) in enumerate(zip(rows, vals)):
            h = 300 * v / hi
            bx = x + ri * bw
            d.rectangle((bx, 400 - h, bx + bw - 6, 400), fill=COLORS[ri % len(COLORS)])
            d.text((bx, 400 - h - 18), f"{v:.2f}", font=fs, fill=FG)
        d.text((x, 410), label, font=fs, fill=FG)
        d.text((x, 428), "higher is better" if up else "lower is better", font=fs, fill=DIM)
    for ri, r in enumerate(rows):
        d.rectangle((W - 220, 60 + ri * 22, W - 206, 74 + ri * 22), fill=COLORS[ri % len(COLORS)])
        d.text((W - 198, 58 + ri * 22), r, font=fs, fill=FG)
    p1 = _name("compare", tag, dt.date.today().isoformat()); im.save(p1)
    # table
    cols = ["run", "clips"] + [lab for _, lab, _ in METRICS]
    lines = [cols] + [[r, str(m.get("clips", ""))] + [f"{m.get(k, 0):.3f}" for k, _, _ in METRICS] for r, m in rows.items()]
    widths = [max(len(str(row[i])) for row in lines) + 2 for i in range(len(cols))]
    im2 = Image.new("RGB", (40 + 9 * sum(widths), 90 + 26 * len(lines)), BG); d2 = ImageDraw.Draw(im2)
    d2.text((20, 12), f"Results on {key}  ({dt.date.today()})", font=fb, fill=FG)
    for li, row in enumerate(lines):
        y = 56 + li * 26
        if li == 0:
            d2.rectangle((14, y - 3, im2.width - 14, y + 21), fill=(240, 242, 246))
        x = 20
        for i, c in enumerate(row):
            d2.text((x, y), str(c), font=_font(15), fill=FG); x += 9 * widths[i]
    p2 = _name("table", tag, dt.date.today().isoformat()); im2.save(p2)
    md = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n" + "".join("| " + " | ".join(r) + " |\n" for r in lines[1:])
    return p1, p2, md


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fasr report")
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--set", default="eval", help="substring of the eval set key, e.g. me_dev/eval")
    a = ap.parse_args(argv)
    for r in a.runs:
        p = curves(r)
        if p:
            print("curves:", p)
    p1, p2, md = compare(a.runs, a.set)
    print("compare:", p1); print("table:", p2)
    res = OUT / "RESULTS.md"
    with res.open("a") as fh:
        fh.write(f"\n## {dt.datetime.now():%Y-%m-%d %H:%M} — {a.set}\n\n{md}\n![compare]({p1.name})\n![table]({p2.name})\n")
    print(md)


if __name__ == "__main__":
    main()
