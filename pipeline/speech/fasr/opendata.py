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

URL = "https://www.openslr.org/resources/12/{part}.tar.gz"


def fetch(part: str, out: Path):
    tgz = out / f"{part}.tar.gz"
    done = out / f".{part}.done"
    if done.exists():
        return
    if not tgz.exists() or tgz.stat().st_size == 0:
        subprocess.run(["curl", "-fL", "-C", "-", "-o", str(tgz), URL.format(part=part)], check=True)
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
