"""Embedding-bag classifier: the same small shape for the router and every expert.

  words  -> per-word rows (int8)      \
  bigrams, negation scope -> hashed rows (int8)  > confidence-weighted means, summed
  context (current node / tags) -> rows (int8)   /
  -> dense d->H, ReLU -> dense H->K -> scores

On the device only the rows a sentence touches are read (a dozen or two), so an expert
can sit in memory-mapped flash and "loading" it is swapping a pointer.
Training-only extra: each word also gets character-trigram vectors (fastText style), so a
word the seed data never used still lands near words that look like it. At export the
trigrams are folded into that word's single row, so they cost nothing on the device.
"""
from __future__ import annotations

import math
import random
import struct

import numpy as np
import torch
import torch.nn as nn

from .lexicon import Lexicon

HASH_BIGRAM, HASH_NEG = 1, 2
NEG_WINDOW = 3


def fnv1a(kind: int, a: int, b: int, buckets: int) -> int:
    h = 0x811C9DC5
    for byte in (kind, a & 0xFF, a >> 8, b & 0xFF, b >> 8):
        h ^= byte
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h % buckets


def features(nb, lex: Lexicon, buckets: int):
    """n-best [[(id, conf)]] -> (word ids, word weights, hash ids, hash weights)."""
    wi, ww, hi, hw = [], [], [], []
    for r, hyp in enumerate(nb[:5]):
        if not hyp:
            continue
        hyp_w = (0.6 ** r) * (sum(c for _, c in hyp) / len(hyp) / 100.0)
        prev, neg_left = None, 0
        for i, c in hyp:
            w = hyp_w * c / 100.0
            if i and w > 0:
                wi.append(i); ww.append(w)
                if prev:
                    hi.append(fnv1a(HASH_BIGRAM, prev, i, buckets)); hw.append(w)
                if neg_left and not lex.is_neg(i):
                    hi.append(fnv1a(HASH_NEG, 0, i, buckets)); hw.append(w)
            if i and lex.is_neg(i):
                neg_left = NEG_WINDOW
            elif neg_left:
                neg_left -= 1
            prev = i or None
    return wi, ww, hi, hw


def trigrams(word: str, buckets: int) -> list[int]:
    w = f"<{word}>"
    return [(hash_str(w[k:k + 3]) % buckets) for k in range(max(1, len(w) - 2))]


def hash_str(s: str) -> int:
    h = 0x811C9DC5
    for ch in s.encode():
        h ^= ch
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


class Net(nn.Module):
    def __init__(self, lex: Lexicon, k: int, d=64, hidden=128, buckets=4096, ctx=0, tri=8192,
                 teacher: np.ndarray | None = None):
        super().__init__()
        self.lex, self.k, self.d, self.hidden, self.buckets, self.nctx = lex, k, d, hidden, buckets, ctx
        V = len(lex)
        self.word = nn.Embedding(V, d)
        nn.init.normal_(self.word.weight, 0, 0.1)
        if teacher is not None:                        # desktop: big-model word vectors, projected to d
            self.word.weight.data[:] = torch.tensor(teacher[:, :d], dtype=torch.float32)
        with torch.no_grad():
            self.word.weight[0].zero_()
        self.tri = nn.EmbeddingBag(tri, d, mode="mean")
        nn.init.normal_(self.tri.weight, 0, 0.1)
        tl = [trigrams(w, tri) for w in lex.words]
        self.register_buffer("tri_ids", torch.tensor([t for ts in tl for t in ts]))
        self.register_buffer("tri_off", torch.tensor([0] + list(np.cumsum([len(t) for t in tl])[:-1])))
        self.hash = nn.EmbeddingBag(buckets, d, mode="sum")
        nn.init.normal_(self.hash.weight, 0, 0.05)
        self.ctx = nn.Embedding(max(ctx, 1), d)
        nn.init.normal_(self.ctx.weight, 0, 0.1)
        self.fc1 = nn.Linear(d, hidden)
        self.fc2 = nn.Linear(hidden, k)
        self.drop = nn.Dropout(0.2)

    def word_table(self):
        t = self.word.weight + self.tri(self.tri_ids, self.tri_off)
        mask = torch.ones(len(self.lex), 1); mask[0] = 0      # UNK reads as nothing
        return t * mask

    def forward(self, batch):
        wt = self.word_table()
        xs = []
        for wi, ww, hi, hw, c in batch:
            x = torch.zeros(self.d)
            if wi:
                w = torch.tensor(ww)
                x = x + (wt[torch.tensor(wi)] * w[:, None]).sum(0) / w.sum()
            if hi:
                w = torch.tensor(hw)
                x = x + self.hash(torch.tensor(hi)[None], per_sample_weights=w[None])[0] / w.sum()
            if c >= 0 and self.nctx:
                x = x + self.ctx.weight[c]
            xs.append(x)
        x = torch.stack(xs)
        return self.fc2(self.drop(torch.relu(self.fc1(x))))


# ---------------- export / int8 reference ----------------

def q_rows(m: np.ndarray):
    s = np.abs(m).max(1) / 127.0
    s[s == 0] = 1.0
    return np.clip(np.round(m / s[:, None]), -127, 127).astype(np.int8), s.astype(np.float32)


class Packed:
    """The exact numbers the device uses (int8 rows + float scales)."""

    def __init__(self, net: Net, class_map: list[int], names: list[str], kind: int):
        with torch.no_grad():
            self.W, self.Ws = q_rows(net.word_table().numpy())
            self.Hh, self.Hs = q_rows(net.hash.weight.numpy())
            self.C, self.Cs = q_rows(net.ctx.weight.numpy()) if net.nctx else (np.zeros((0, net.d), np.int8), np.zeros(0, np.float32))
            self.W1, self.s1 = q_rows(net.fc1.weight.numpy()); self.b1 = net.fc1.bias.numpy().astype(np.float32)
            self.W2, self.s2 = q_rows(net.fc2.weight.numpy()); self.b2 = net.fc2.bias.numpy().astype(np.float32)
        self.lex, self.buckets, self.kind = net.lex, net.buckets, kind
        self.d, self.hidden, self.k = net.d, net.hidden, net.k
        self.class_map, self.names = class_map, names

    def logits(self, nb, ctx=-1):
        wi, ww, hi, hw = features(nb, self.lex, self.buckets)
        x = np.zeros(self.d, np.float32)
        if wi:
            w = np.array(ww, np.float32)
            x += (self.W[wi].astype(np.float32) * (self.Ws[wi] * w)[:, None]).sum(0) / w.sum()
        if hi:
            w = np.array(hw, np.float32)
            x += (self.Hh[hi].astype(np.float32) * (self.Hs[hi] * w)[:, None]).sum(0) / w.sum()
        if ctx >= 0 and len(self.C):
            x += self.C[ctx].astype(np.float32) * self.Cs[ctx]
        h = np.maximum(0, (self.W1.astype(np.float32) @ x) * self.s1 + self.b1)
        return (self.W2.astype(np.float32) @ h) * self.s2 + self.b2

    def to_bytes(self) -> bytes:
        V, B, C = len(self.W), len(self.Hh), len(self.C)
        names = b"".join(n.encode()[:31] + b"\0" for n in self.names)
        hdr = struct.pack("<4sHHHHHHHHHH", b"VXP1", 1, self.kind, V, B, C, self.d, self.hidden, self.k,
                          len(names), 0)
        cls = bytes(self.lex.cls)
        parts = [hdr, cls + b"\0" * (-len(cls) % 4)]
        for q, s in ((self.W, self.Ws), (self.Hh, self.Hs), (self.C, self.Cs)):
            parts += [s.tobytes(), q.tobytes()]
        parts += [self.s1.tobytes(), self.b1.tobytes(), self.W1.tobytes(),
                  self.s2.tobytes(), self.b2.tobytes(), self.W2.tobytes(),
                  np.array(self.class_map, np.uint16).tobytes(), names]
        blob = b"".join(parts)
        return blob + b"\0" * (-len(blob) % 4)


def softmax(z):
    z = z - z.max()
    e = np.exp(z)
    return e / e.sum()
