"""Measure how the device mic colours sound, so synthetic training voices can be made to sound like it.

    python -m fasr micmatch [--device $WORK/speech/me_dev/train.jsonl] [--ref $WORK/speech/tts/train.jsonl]

Average log spectrum of speech frames on the device mic minus the same for the reference (TTS)
clips = the mic's EQ curve in dB per frequency. Written to $WORK/speech/mic_eq.json; data.Augment
then applies it to training audio at a random strength, plus a random high cut (a waterproof
membrane in a printed case muffles the highs) and level, so the model expects the real mic without
being tied to one exact case. Re-run with clips recorded through the final case.
"""
from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path

import numpy as np

from .data import load_wav

WORK = Path(os.environ.get("WORK", "/mnt/data/first-aid-gpu"))
NFFT = 512


def avg_spectrum(manifest: Path, n: int, seed: int = 1) -> np.ndarray:
    recs = [json.loads(l) for l in manifest.read_text().splitlines() if l.strip()]
    recs = random.Random(seed).sample(recs, min(n, len(recs)))
    acc, cnt = np.zeros(NFFT // 2 + 1), 0
    win = np.hanning(NFFT)
    for r in recs:
        w = Path(r["wav"]) if Path(r["wav"]).is_absolute() else manifest.parent / r["wav"]
        x = load_wav(str(w))
        if len(x) < NFFT:
            continue
        fr = np.lib.stride_tricks.sliding_window_view(x, NFFT)[::NFFT // 2] * win
        p = np.abs(np.fft.rfft(fr, axis=1)) ** 2
        e = p.sum(1)
        keep = e > np.percentile(e, 60)          # loudest 40% of frames: speech, not pauses
        acc += np.log10(p[keep] + 1e-10).sum(0); cnt += int(keep.sum())
    return 10 * acc / max(1, cnt)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fasr micmatch")
    ap.add_argument("--device", type=Path, default=WORK / "speech" / "me_dev" / "train.jsonl")
    ap.add_argument("--ref", type=Path, default=WORK / "speech" / "tts" / "train.jsonl")
    ap.add_argument("--n", type=int, default=600)
    ap.add_argument("--out", type=Path, default=WORK / "speech" / "mic_eq.json")
    a = ap.parse_args(argv)
    dev, ref = avg_spectrum(a.device, a.n), avg_spectrum(a.ref, a.n)
    eq = dev - ref
    eq = np.convolve(eq, np.ones(9) / 9, mode="same")          # smooth: a curve, not noise
    f = np.fft.rfftfreq(NFFT, 1 / 16000)
    band = (f > 150) & (f < 6000)
    eq -= eq[band].mean()                                      # shape only; level is randomised separately
    eq = np.clip(eq, -10, 10)
    a.out.write_text(json.dumps({"freq_hz": f.round(1).tolist(), "eq_db": eq.round(2).tolist(),
                                 "device": str(a.device), "ref": str(a.ref)}, indent=0))
    for hz in (100, 250, 500, 1000, 2000, 3000, 4000, 6000, 7500):
        print(f"{hz:5d} Hz {eq[np.argmin(abs(f - hz))]:+6.1f} dB")
    print("wrote", a.out)


if __name__ == "__main__":
    main()
