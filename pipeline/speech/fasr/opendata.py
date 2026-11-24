"""Open speech for pretraining: LibriSpeech (CC BY 4.0, read English audiobooks, ~250 speakers
in train-clean-100). The model learns general English sounds here, then is fine-tuned on
first-aid speech. Manifests point at the original .flac files (soundfile reads them).

    python -m fasr opendata --out $WORK/speech/librispeech [--parts train-clean-100 dev-clean]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tarfile
from pathlib import Path

import soundfile as sf

import os

# Mirrors in order (www.openslr.org gave ~1 MB/s from Charlie's PC, the EU mirror ~3.6 MB/s).
MIRRORS = os.environ.get("FASR_OPENSLR", "https://openslr.elda.org https://www.openslr.org https://openslr.trmal.net").split()
URL = "{host}/resources/12/{part}.tar.gz"


def fetch(part: str, out: Path):
    tgz = out / f"{part}.tar.gz"
    done = out / f".{part}.done"
    if done.exists():
        return
    if not tgz.exists():                       # downloads go to .part and are renamed only when complete,
        part_f = tgz.with_suffix(".gz.part")   # so an interrupted run never extracts a half file
        for i, host in enumerate(MIRRORS):     # -C - resumes a partial file from any mirror (same tarball)
            r = subprocess.run(["curl", "-fL", "--retry", "3", "--speed-limit", "100000", "--speed-time", "60",
                                "-C", "-", "-o", str(part_f), URL.format(host=host, part=part)])
            if r.returncode == 0:
                break
            if i == len(MIRRORS) - 1:
                raise SystemExit(f"could not download {part} from {MIRRORS}")
        part_f.rename(tgz)
    with tarfile.open(tgz) as t:
        t.extractall(out, filter="data")
    done.touch()
    tgz.unlink()


def manifest(part: str, out: Path) -> Path:
    root = out / "LibriSpeech" / part
    m = out / f"{'dev' if part.startswith('dev') else 'train'}-{part}.jsonl"
    with m.open("w") as f:
        for tr in sorted(root.rglob("*.trans.txt")):
            for line in tr.read_text().splitlines():
                uid, _, text = line.partition(" ")
                wav = tr.parent / f"{uid}.flac"
                secs = sf.info(str(wav)).duration
                f.write(json.dumps({"wav": str(wav), "text": text.lower(), "secs": round(secs, 2)}) + "\n")
    return m


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fasr opendata")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--parts", nargs="*", default=["dev-clean", "train-clean-100"])
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    for p in a.parts:
        print("fetching", p, flush=True)
        fetch(p, a.out)
        print("manifest", manifest(p, a.out), flush=True)
    # one train.jsonl / dev.jsonl for convenience
    for kind in ("train", "dev"):
        parts = sorted(a.out.glob(f"{kind}-*.jsonl"))
        with (a.out / f"{kind}.jsonl").open("w") as f:
            for p in parts:
                f.write(p.read_text())
    print("done")
