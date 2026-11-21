"""Run captures for GitHub: every training run, simulation or test saves a terminal-style PNG
(Charlie 2026-11-17), named for what it shows + date + run + test number:

    pipeline/speech/docs/runs/2026-11-17_evaluate_m_test01.png

Automatic for `python -m fasr train|evaluate|pagescore|slot|calibrate|adapt|export` (the last
~70 lines of the command's output; FASR_NO_SNAP=1 turns it off). By hand, for any log or command:

    python -m fasr snap --kind router-sim --run firstaid --log out.txt
    python -m fasr snap --kind bench --run m -- some command args

Only metrics and progress lines belong in a capture: never manual text, prompt lists or
transcripts of Charlie's recordings (devrec/record/synth/whisper are never captured).
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "pipeline" / "speech" / "docs" / "runs"
CAPTURED = {"train", "evaluate", "pagescore", "slot", "calibrate", "adapt", "export", "export_int8"}
MAX_LINES, WRAP = 70, 110
BG, FG, DIM, ACC = (24, 26, 33), (220, 223, 228), (130, 136, 150), (110, 200, 140)


def _font(size=15):
    from PIL import ImageFont
    for f in ("DejaVuSansMono.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
              "LiberationMono-Regular.ttf", "Consolas.ttf", "Menlo.ttc"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _name(kind: str, run: str, date: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    safe = lambda s: re.sub(r"[^A-Za-z0-9.-]+", "-", s).strip("-") or "na"
    stem = f"{date}_{safe(kind)}_{safe(run)}_test"
    n = 1 + max([int(m.group(1)) for p in OUT.glob(stem + "*.png") if (m := re.search(r"_test(\d+)\.png$", p.name))] or [0])
    return OUT / f"{stem}{n:02d}.png"


def render(text: str, kind: str, run: str, cmd: str = "") -> Path:
    from PIL import Image, ImageDraw
    now = dt.datetime.now()
    path = _name(kind, run, now.strftime("%Y-%m-%d"))
    lines = []
    for l in text.replace("\r", "\n").splitlines():
        lines += [l[i:i + WRAP] for i in range(0, max(1, len(l)), WRAP)] or [""]
    if len(lines) > MAX_LINES:
        lines = [f"... ({len(lines) - MAX_LINES} earlier lines)"] + lines[-MAX_LINES:]
    head = f"{path.stem}   {now:%Y-%m-%d %H:%M}"
    f, fs = _font(15), _font(13)
    lh, pad = 19, 18
    w = pad * 2 + 9 * WRAP
    h = pad * 2 + lh * (len(lines) + 3)
    im = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(im)
    for i, c in enumerate(((237, 106, 94), (245, 191, 79), (98, 197, 84))):
        d.ellipse((pad + i * 18, pad, pad + i * 18 + 11, pad + 11), fill=c)
    d.text((pad + 64, pad - 2), head, font=fs, fill=DIM)
    y = pad + lh * 2
    if cmd:
        d.text((pad, y - lh + 2), "$ " + cmd[:WRAP - 2], font=f, fill=ACC)
        y += 4
    for l in lines:
        d.text((pad, y), l, font=f, fill=FG)
        y += lh
    im.save(path)
    return path


class Tee(io.TextIOBase):
    def __init__(self, real):
        self.real, self.buf = real, io.StringIO()

    def write(self, s):
        self.buf.write(s)
        return self.real.write(s)

    def flush(self):
        self.real.flush()


def run_arg(argv) -> str:
    for i, x in enumerate(argv):
        if x == "--run" and i + 1 < len(argv):
            return argv[i + 1]
        if x.startswith("--run="):
            return x.split("=", 1)[1]
    return "na"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fasr snap")
    ap.add_argument("--kind", required=True, help="what it shows: train, evaluate, router-sim, bench...")
    ap.add_argument("--run", default="na")
    ap.add_argument("--log", type=Path, help="render the tail of this log file")
    ap.add_argument("cmd", nargs=argparse.REMAINDER, help="-- command to run and capture")
    a = ap.parse_args(argv)
    cmd = [c for c in a.cmd if c != "--"]
    if a.log:
        text, shown = a.log.read_text(errors="replace"), f"tail {a.log.name}"
    elif cmd:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        text, shown = p.stdout, " ".join(cmd)
        sys.stdout.write(text)
    else:
        text, shown = sys.stdin.read(), ""
    print("capture:", render(text, a.kind, a.run, shown))


if __name__ == "__main__":
    main()
