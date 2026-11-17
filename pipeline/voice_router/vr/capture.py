"""Terminal-style capture of a run, for GitHub (Charlie 11-17: every training run or simulation
gets a picture of it working, named for what it shows plus date / run / test number).

    python -m vr.intake ... --sim seeds.yaml | python -m vr.capture --what form-sim-dev \\
        --run v16b --test 3 [--cmd "python -m vr.intake ... --sim dev"] [--out ../../docs/runs]

writes docs/runs/<YYYY-MM-DD>_<what>_run-<run>_test-<n>.png (and the same .txt beside it).
Only paste scores and the engine's own words: never manual passages (personal content stays off
GitHub). Lines that look like quoted card text can be dropped with --grep-out.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
         "DejaVuSansMono.ttf", "Menlo.ttc", "consola.ttf"]
BG, FG, DIM, OK, BAD, HEAD = "#0d1117", "#e6edf3", "#7d8590", "#3fb950", "#f85149", "#58a6ff"


def _font(size: int):
    for f in FONTS:
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _colour(line: str) -> str:
    if re.search(r"WRONG [1-9]|WRONG: |FAIL|Error|never: [1-9]", line):
        return BAD
    if re.search(r"passed|\bright\b|top3|turn \(|by voice turn", line):
        return OK
    if line.startswith(("$", "#")):
        return HEAD
    return FG


def render(text: str, title: str, cmd: str | None, out: Path, width_chars: int = 110, size: int = 15) -> Path:
    font = _font(size)
    lines = []
    for raw in text.rstrip("\n").splitlines() or [""]:
        while len(raw) > width_chars:
            lines.append(raw[:width_chars]); raw = "  " + raw[width_chars:]
        lines.append(raw)
    head = [title] + ([f"$ {cmd}"] if cmd else [])
    lh = int(size * 1.45)
    cw = font.getlength("M") if hasattr(font, "getlength") else size * 0.6
    w = int(cw * width_chars) + 48
    h = lh * (len(head) + len(lines) + 1) + 64
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    for i, c in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")):     # window dots
        d.ellipse((16 + i * 22, 14, 30 + i * 22, 28), fill=c)
    y = 44
    d.text((24, y), head[0], font=font, fill=DIM); y += lh
    for hl in head[1:]:
        d.text((24, y), hl, font=font, fill=HEAD); y += lh
    y += lh // 2
    for ln in lines:
        d.text((24, y), ln, font=font, fill=_colour(ln)); y += lh
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    out.with_suffix(".txt").write_text((("$ " + cmd + "\n") if cmd else "") + text)
    return out


def name_for(what: str, run: str, test: str | int, date: str | None = None) -> str:
    date = date or dt.date.today().isoformat()
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", what).strip("-")
    return f"{date}_{slug}_run-{run}_test-{test}.png"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vr.capture")
    ap.add_argument("--what", required=True, help="what the capture shows, e.g. form-sim-dev")
    ap.add_argument("--run", required=True, help="run id, e.g. v16b or a commit")
    ap.add_argument("--test", required=True, help="test number within the run")
    ap.add_argument("--cmd", help="the command line, shown at the top")
    ap.add_argument("--date", help="YYYY-MM-DD (default today)")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[3] / "docs" / "runs")
    ap.add_argument("--grep-out", action="append", default=[], help="regex: drop matching lines")
    a = ap.parse_args(argv)
    text = sys.stdin.read()
    if a.grep_out:
        text = "\n".join(l for l in text.splitlines() if not any(re.search(g, l) for g in a.grep_out))
    name = name_for(a.what, a.run, a.test, a.date)
    title = f"{name[:-4]}   ({a.date or dt.date.today().isoformat()}, run {a.run}, test {a.test})"
    print(render(text, title, a.cmd, a.out / name))


if __name__ == "__main__":
    main()
