"""Calibrate the recognizer's reject score (0-100) on held-out audio.

    python -m fasr calibrate --run firstaid [--max-false-reject 0.05]

The decoder's raw reject is the n-best mass of all-<unk> hypotheses. That only fires on sounds
that are not lexicon words, so it misses noise that decodes as short words and speech that is
partly in the lexicon. Here a few cheap per-clip features (all computable on the device from
the same posteriors and n-best) are combined by a logistic fit:

    junk     raw all-<unk> mass                  unk      share of <unk> in the top hypothesis
    lowconf  1 - mean word confidence (top hyp)  empty    top hypothesis has no words
    speech   share of frames that are not blank  acoustic mean best non-blank log-prob on speech frames
    perword  words per second of speech

Sets: ON = first-aid speech (TTS dev voices, held-out TTS sentences, half of Charlie's EVAL clips);
REJECT = noise with no speech (Charlie's NOISE takes + synthetic wind/water/breath), out-of-domain
speech (LibriSpeech dev), off-topic chatter (topics.yaml reject tests, agy's off-topic lines).
Even/odd split by clip: fit on one half, report on the other. The threshold is set so at most
--max-false-reject of ON clips are rejected on the fit half. Writes runs/<run>/reject_calib.json;
decode.calibrated_reject() applies it. Off-topic sentences made of ordinary lexicon words
("play some music") cannot be told apart acoustically: that is the router's reject class.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from pathlib import Path

import numpy as np
import torch
import yaml

from . import lexicon as lx
from .data import load_wav
from .decode import Params, Trie, nbest, reject_features
from .evaluate import load_model
from .features import logmel

WORK = Path(os.environ.get("WORK", "/mnt/data/first-aid-gpu"))
SR = 16000
AGY_EVAL = Path(__file__).resolve().parents[1] / "out" / "agy_eval.txt"


def clips(manifest: Path, kind: str, keep=lambda r: True, n: int = 0, seed: int = 3):
    recs = [json.loads(l) for l in manifest.read_text().splitlines() if l.strip()]
    recs = [r for r in recs if keep(r)]
    if n and len(recs) > n:
        recs = random.Random(seed).sample(recs, n)
    for r in recs:
        w = Path(r["wav"]) if Path(r["wav"]).is_absolute() else manifest.parent / r["wav"]
        yield kind, str(w), r.get("text", "")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fasr calibrate")
    ap.add_argument("--run", default="firstaid")
    ap.add_argument("--max-false-reject", type=float, default=0.05)
    ap.add_argument("--beam", type=int, default=24)
    a = ap.parse_args(argv)
    sp = WORK / "speech"
    rows = yaml.safe_load(lx.LEXICON.read_text())["words"]
    trie = Trie.from_lexicon(rows)
    model = load_model(a.run)

    topics = yaml.safe_load((lx.CONTENT / "voice" / "topics.yaml").read_text())
    off_text = {" ".join(lx.tokenize(s)) for s in topics.get("reject", {}).get("test", [])}
    if AGY_EVAL.exists():   # agy wrote 140 first-aid lines, then 10 off-topic ones
        off_text |= {" ".join(lx.tokenize(s)) for s in AGY_EVAL.read_text().splitlines()[140:] if s.strip()}
    is_off = lambda r: " ".join(lx.tokenize(r.get("text", ""))) in off_text

    items = []
    items += list(clips(sp / "tts" / "dev.jsonl", "on"))
    items += list(clips(sp / "tts" / "eval.jsonl", "on", lambda r: not is_off(r), n=800))
    items += list(clips(sp / "tts" / "eval.jsonl", "off_topic", is_off))
    items += list(clips(sp / "me_dev" / "eval.jsonl", "on_me", lambda r: not is_off(r)))
    items += list(clips(sp / "me_dev" / "eval.jsonl", "off_topic_me", is_off))
    items += list(clips(sp / "librispeech" / "dev.jsonl", "other_speech", n=400))
    noise_dir = sp / "me_dev" / "noise"
    noise_wavs = sorted(noise_dir.glob("*.wav")) if noise_dir.exists() else []
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "gpu"))
    from make_clips import noise as make_noise
    rng = np.random.default_rng(7)

    feats, kinds = [], []
    def run(x: np.ndarray, kind: str):
        f = torch.from_numpy(logmel(x))[None]
        with torch.no_grad():
            lp = model(f)[0].numpy()
        hyp = nbest(lp, trie, Params(beam=a.beam))
        feats.append(reject_features(lp, hyp)); kinds.append(kind)

    for i, (kind, wav, _) in enumerate(items):
        run(load_wav(wav), kind)
        if i % 300 == 0:
            print(f"{i}/{len(items)} clips", flush=True)
    for w in noise_wavs:                       # Charlie's real noise: cut into 3 s pieces
        x = load_wav(str(w))
        for s in range(0, max(1, len(x) - 3 * SR), 3 * SR):
            run(x[s:s + 3 * SR], "noise_me")
    for k in range(240):                       # synthetic noise, no speech, at varied levels
        kind = ["wind", "water", "breath"][k % 3]
        x = make_noise(kind, int(SR * rng.uniform(1.5, 4.0)), rng) * 10 ** rng.uniform(-2.5, -0.7)
        run(x.astype(np.float32), "noise_syn")

    X = np.array(feats, dtype=np.float64); K = np.array(kinds)
    ON = np.isin(K, ["on", "on_me"])
    y = (~ON).astype(np.float64)
    fit = np.arange(len(K)) % 2 == 0
    mu, sd = X[fit].mean(0), X[fit].std(0) + 1e-6
    Z = (X - mu) / sd
    # class-balanced logistic regression, plain gradient descent
    w = np.zeros(Z.shape[1]); b = 0.0
    wt = np.where(y == 1, 0.5 / max(1, y[fit].sum()), 0.5 / max(1, (1 - y[fit]).sum()))
    for _ in range(4000):
        p = 1 / (1 + np.exp(-(Z[fit] @ w + b)))
        g = (p - y[fit]) * wt[fit]
        w -= 0.5 * (Z[fit].T @ g + 1e-3 * w); b -= 0.5 * g.sum()
    score = 1 / (1 + np.exp(-(Z @ w + b)))
    on_fit = np.sort(score[fit & ON])
    thr = float(on_fit[int(math.ceil((1 - a.max_false_reject) * len(on_fit))) - 1])

    raw = X[:, 0]
    raw_thr = float(np.sort(raw[fit & ON])[int(math.ceil((1 - a.max_false_reject) * (fit & ON).sum())) - 1])
    test = ~fit
    rep = {"threshold": round(thr, 4), "max_false_reject": a.max_false_reject, "per_set_test_half": {}}
    for k in ["on", "on_me", "noise_me", "noise_syn", "other_speech", "off_topic", "off_topic_me"]:
        m = test & (K == k)
        if m.any():
            rep["per_set_test_half"][k] = {
                "clips": int(m.sum()),
                "rejected_calibrated": round(float((score[m] > thr).mean()), 3),
                "rejected_raw_same_false_reject": round(float((raw[m] > raw_thr).mean()), 3),
            }
    names = ["junk", "unk", "lowconf", "empty", "speech", "acoustic", "perword"]
    calib = {"features": names, "mean": mu.round(6).tolist(), "std": sd.round(6).tolist(),
             "weights": w.round(6).tolist(), "bias": round(float(b), 6), "threshold": round(thr, 6),
             "lexicon_words": len(rows), "note": "reject = 100 * sigmoid(((f-mean)/std) . weights + bias); "
             "the threshold maps to 50 in calibrated_reject()"}
    out = WORK / "speech" / "runs" / a.run / "reject_calib.json"
    out.write_text(json.dumps(calib, indent=1))
    rep["weights"] = dict(zip(names, [round(v, 3) for v in w]))
    print(json.dumps(rep, indent=1))
    print("wrote", out)


if __name__ == "__main__":
    main()
