"""Per-user voice enrollment: fit a tiny speaker profile without retraining the model.

The base model stays frozen (it lives in flash). Enrollment = the new user reads ~30
prompts (2-3 minutes) on the device, and the device fits a profile:

  level 1  "norm":  the user's own feature mean/std (80 floats). Cheap: one pass of
                    feature statistics. Fixes mic distance, voice pitch, accent tilt a bit.
  level 2  "head":  + head bias and the last FiLM scale/shift (41 + 384 numbers), fitted
                    from forced alignments of the known prompts. Needs ONE forward pass per
                    clip (cache the last layer's output) and then a small softmax regression
                    on the cached frames: no backprop through the conv stack.
  level 3  "film":  + FiLM scale/shift after every block (~4.8 k numbers), fitted by backprop
                    through the frozen network. Heaviest; on the S3 this is minutes of work
                    and ~1-2 MB of PSRAM for activations, so it is the "maybe" level.

Profile size in flash: level 2 ~0.5 KB, level 3 ~10 KB (fp16), per user.
Later, the user's pick-from-3 corrections can be added as more enrollment clips (same fit).

This script simulates enrollment on the PC with the same math, to measure the gain:

    python -m fasr adapt --run firstaid --enroll $WORK/speech/me/train.jsonl --n 30 \
        --test $WORK/speech/me/eval.jsonl --level head
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
import yaml

from . import lexicon as lx
from .data import G2P, load_wav
from .decode import Trie, nbest
from .evaluate import load_model
from .features import logmel
from .phones import BLANK
from .train import edit_distance

WORK = Path(os.environ.get("WORK", "/mnt/data/first-aid-gpu"))


def ctc_align(logp: np.ndarray, y: list[int]) -> np.ndarray | None:
    """Viterbi forced alignment: best frame -> token path for target y (blanks interleaved)."""
    T = logp.shape[0]
    ext = [BLANK]
    for t in y:
        ext += [t, BLANK]
    S = len(ext)
    if T < len(y):
        return None
    NEG = -1e30
    dp = np.full((T, S), NEG)
    bp = np.zeros((T, S), np.int32)
    dp[0, 0] = logp[0, ext[0]]
    if S > 1:
        dp[0, 1] = logp[0, ext[1]]
    for t in range(1, T):
        for s in range(S):
            cands = [(dp[t - 1, s], s)]
            if s >= 1:
                cands.append((dp[t - 1, s - 1], s - 1))
            if s >= 2 and ext[s] != BLANK and ext[s] != ext[s - 2]:
                cands.append((dp[t - 1, s - 2], s - 2))
            v, src = max(cands)
            dp[t, s] = v + logp[t, ext[s]]
            bp[t, s] = src
    s = S - 1 if dp[T - 1, S - 1] >= dp[T - 1, S - 2] else S - 2
    path = np.zeros(T, np.int64)
    for t in range(T - 1, -1, -1):
        path[t] = ext[s]
        s = bp[t, s]
    return path


def load_set(path: Path, g2p: G2P) -> list[dict]:
    out = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        wav = Path(r["wav"]) if Path(r["wav"]).is_absolute() else Path(path).parent / r["wav"]
        y = g2p.targets(r["text"])
        if y:
            out.append({"f": torch.from_numpy(logmel(load_wav(str(wav)))), "y": y, "text": r["text"]})
    return out


@torch.no_grad()
def wer(model, items, trie, ids, mu=None, sd=None) -> float:
    err = n = 0
    for it in items:
        lp = model(it["f"][None], mu, sd)[0].numpy()
        h = nbest(lp, trie)
        top = h["h"][0]["ids"] if h["n"] else []
        ref = [ids.get(w, 0) for w in lx.tokenize(it["text"])]
        err += edit_distance(top, ref); n += len(ref)
    return err / max(1, n)


def enroll(model, items, level: str, epochs: int = 30, l2: float = 1e-2):
    """Returns (mu, sd) and fills model.film / model.head_bias in place."""
    allf = torch.cat([it["f"] for it in items])
    mu, sd = allf.mean(0), allf.std(0) + 1e-3
    if level == "norm":
        return mu, sd
    with torch.no_grad():
        aligned = []
        for it in items:
            lp = model(it["f"][None], mu, sd)[0].numpy()
            path = ctc_align(lp, it["y"])
            if path is not None:
                aligned.append((it, torch.from_numpy(path)))
    if level == "head":
        # cache the frozen network's last features once, then fit last FiLM + head bias
        model.film.zero_(); model.head_bias.zero_()
        last = len(model.film) - 1
        with torch.no_grad():
            X = torch.cat([model.features(it["f"][None], mu, sd)[0].T for it, _ in aligned])  # (frames, C)
            # features() applied film[last]=identity; undo nothing needed
        Y = torch.cat([p for _, p in aligned])
        g = torch.zeros(2, X.shape[1], requires_grad=True)
        hb = torch.zeros(model.head_bias.shape, requires_grad=True)
        opt = torch.optim.Adam([g, hb], lr=1e-2)
        W, b = model.head.weight[:, :, 0].detach(), model.head.bias.detach()
        for _ in range(epochs):
            z = (X * (1 + g[0]) + g[1]) @ W.T + b + hb
            loss = F.cross_entropy(z, Y) + l2 * (g.pow(2).sum() + hb.pow(2).sum())
            opt.zero_grad(); loss.backward(); opt.step()
        with torch.no_grad():
            model.film[last].copy_(g); model.head_bias.copy_(hb)
        return mu, sd
    # level "film": every FiLM slot by backprop through the frozen net
    film = torch.zeros_like(model.film, requires_grad=True)
    hb = torch.zeros_like(model.head_bias, requires_grad=True)
    opt = torch.optim.Adam([film, hb], lr=5e-3)
    for p in model.parameters():
        p.requires_grad_(False)
    for _ in range(max(1, epochs // 5)):
        random.shuffle(aligned)
        for it, path in aligned:
            model.film = film; model.head_bias = hb   # temporarily as tensors with grad
            lp = model(it["f"][None], mu, sd)[0]
            loss = F.nll_loss(lp, path) + l2 * (film.pow(2).sum() + hb.pow(2).sum())
            opt.zero_grad(); loss.backward(); opt.step()
    model.film = film.detach(); model.head_bias = hb.detach()
    return mu, sd


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fasr adapt")
    ap.add_argument("--run", required=True)
    ap.add_argument("--enroll", required=True, help="the user's prompted clips (manifest)")
    ap.add_argument("--test", required=True, help="the SAME user's held-out clips")
    ap.add_argument("--n", type=int, default=30, help="enrollment prompts used (~5 s each)")
    ap.add_argument("--level", choices=["norm", "head", "film", "all"], default="all")
    a = ap.parse_args(argv)
    rows = yaml.safe_load(lx.LEXICON.read_text())["words"]
    ids = {r["word"]: r["id"] for r in rows}
    trie = Trie.from_lexicon(rows)
    g2p = G2P()
    en = load_set(Path(a.enroll), g2p)
    random.Random(0).shuffle(en)
    en = en[: a.n]
    te = load_set(Path(a.test), g2p)
    secs = sum(len(it["f"]) for it in en) / 100
    base = load_model(a.run)
    res = {"enroll_clips": len(en), "enroll_secs": round(secs), "test_clips": len(te),
           "wer_base": round(wer(base, te, trie, ids), 3)}
    for lvl in (["norm", "head", "film"] if a.level == "all" else [a.level]):
        m = load_model(a.run)
        t0 = time.time()
        mu, sd = enroll(m, en, lvl)
        res[f"wer_{lvl}"] = round(wer(m, te, trie, ids, mu, sd), 3)
        res[f"fit_secs_pc_{lvl}"] = round(time.time() - t0, 1)
        n = 80 + (0 if lvl == "norm" else m.head_bias.numel() + (2 * m.film.shape[2] if lvl == "head" else m.film.numel()))
        res[f"profile_bytes_fp16_{lvl}"] = 2 * n
    print(json.dumps(res, indent=1))
    (WORK / "speech" / "runs" / a.run / "adapt.json").write_text(json.dumps(res, indent=1))
