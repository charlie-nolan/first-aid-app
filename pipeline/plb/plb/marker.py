"""Figure extraction via Marker (the cyberdeck project's extractor), as a plb figure-cmd.

    plb extract ATP3-50.21 --figure-cmd "python -m plb.marker"
    plb extract ATP3-50.21 --figure-cmd "python -m plb.marker --marker-out ~/subaru_cyberdeck/test_batch_out"

Called as `python -m plb.marker [opts] PDF OUTDIR`. It runs `marker_single` (same local
llamacpp/CUDA settings as the cyberdeck batch script) unless --marker-out points at an
existing Marker output folder. Then it maps Marker's images to the manual's figure numbers
by the "Figure X-Y." caption next to each image in Marker's markdown, and writes plb's
figures.json + figures/*.png (grayscale; plb's own picture step does the 1-bit pass).

Only figures come from Marker. Passage text still comes from plb's direct PDF text
extraction, so an OCR or model error can never change what a passage says.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

from .extract import FIG_RE, RESTRICTED_RE, Figure, normalize

IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
PAGE_RE = re.compile(r"_page_(\d+)_")


def marker_env() -> dict:
    env = os.environ.copy()
    env.setdefault("SURYA_INFERENCE_BACKEND", "llamacpp")
    env.setdefault("INFERENCE_BACKEND", "llamacpp")
    env.setdefault("CUDA_VISIBLE_DEVICES", "0")
    env.setdefault("TORCH_DEVICE", "cuda")
    return env


def run_marker(pdf: Path, out_root: Path) -> Path:
    if not shutil.which("marker_single"):
        sys.exit("marker_single not on PATH (activate marker_env: conda activate marker_env)")
    out_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["marker_single", str(pdf), "--output_dir", str(out_root)], env=marker_env(),
                   check=True)
    return out_root / pdf.stem


def _clean(line: str) -> str:
    return normalize(re.sub(r"[*_#>`]+", "", line)).strip()


def map_figures(marker_dir: Path, manual_id: str, out_dir: Path) -> list[Figure]:
    mds = sorted(marker_dir.glob("*.md"))
    if not mds:
        sys.exit(f"no Marker markdown in {marker_dir}")
    lines = mds[0].read_text(errors="replace").splitlines()
    fig_dir = out_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    figs: list[Figure] = []
    used: set[str] = set()
    uncaptioned = 0
    for i, line in enumerate(lines):
        for ref in IMG_RE.findall(line):
            src = marker_dir / ref
            if not src.exists():
                continue
            pm = PAGE_RE.search(ref)
            page = int(pm.group(1)) + 1 if pm else 0          # Marker pages are 0-based
            # Army style puts the caption below the figure; also look just above.
            near = [_clean(l) for l in lines[i + 1:i + 5] if l.strip()][:3] + \
                   [_clean(l) for l in reversed(lines[max(0, i - 3):i]) if l.strip()][:2]
            m = next((FIG_RE.match(t) for t in near if FIG_RE.match(t)), None)
            if m and m["num"] not in used:
                num, title = m["num"], m["title"].strip()
                fid = f"{manual_id}/fig/{num}"
                used.add(num)
            else:
                uncaptioned += 1
                num, title = f"p{page}-{uncaptioned}", ""
                fid = f"{manual_id}/img/{num}"
            png = fig_dir / f"{fid.replace('/', '_')}.png"
            with Image.open(src) as im:
                im.convert("L").save(png)
            restricted = bool(RESTRICTED_RE.search(" ".join(near)))
            figs.append(Figure(fid, manual_id, num, title, page, [], png.name, restricted, 0))
    return figs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="plb.marker")
    ap.add_argument("pdf")
    ap.add_argument("outdir")
    ap.add_argument("--marker-out", help="existing Marker output root (skip running Marker)")
    ap.add_argument("--manual-id", help="default: name of OUTDIR (build/extract/<ID>)")
    a = ap.parse_args(argv)
    pdf, out = Path(a.pdf), Path(a.outdir)
    manual_id = a.manual_id or out.name
    if a.marker_out:
        mdir = Path(a.marker_out).expanduser() / pdf.stem
        if not mdir.exists():                       # cyberdeck layout: one folder per PDF stem
            mdir = Path(a.marker_out).expanduser()
    else:
        mdir = run_marker(pdf, out / "marker")
    figs = map_figures(mdir, manual_id, out)
    from dataclasses import asdict
    (out / "figures.json").write_text(json.dumps([asdict(f) for f in figs], indent=1))
    cap = sum(1 for f in figs if "/fig/" in f.id)
    print(f"{manual_id}: {len(figs)} Marker images, {cap} matched to figure captions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
