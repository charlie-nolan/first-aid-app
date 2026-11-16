"""Fake recognizer output for training and tests.

A closed-vocabulary recognizer makes three kinds of mistakes: it swaps a word for one
that sounds alike, drops a word, or hears a short word that wasn't said. It also returns
several guesses (n-best). Until the recognizer thread's bench data exists we model sound-
alikes with spelling similarity (difflib); swap in phoneme distance from lexicon.yaml later.
"""
from __future__ import annotations

import difflib
import random

from .lexicon import Lexicon

FILLERS = ["the", "a", "and", "uh", "is", "it", "my", "his", "her", "to"]


class Confusions:
    def __init__(self, lex: Lexicon, k: int = 4, cutoff: float = 0.6):
        words = lex.words[1:]
        self.near: dict[int, list[int]] = {}
        for i, w in enumerate(lex.words):
            if i == 0:
                continue
            cands = difflib.get_close_matches(w, words, n=k + 1, cutoff=cutoff)
            self.near[i] = [lex.ids[c] for c in cands if c != w][:k]
        self.fill = [lex.ids[w] for w in FILLERS if w in lex.ids]


def hypothesis(ids: list[int], conf: Confusions, level: float, rng: random.Random):
    """One noisy guess: list of (word_id, confidence 0-100)."""
    out = []
    for i in ids:
        r = rng.random()
        if r < level * 0.35:                       # dropped
            continue
        if r < level * 0.85 and conf.near.get(i):  # sound-alike swap
            out.append((rng.choice(conf.near[i]), rng.randint(25, 70)))
            continue
        out.append((i, rng.randint(55, 100) if level else 100))
        if conf.fill and rng.random() < level * 0.15:
            out.append((rng.choice(conf.fill), rng.randint(20, 60)))
    return out or [(0, 10)]


def nbest(ids: list[int], conf: Confusions, level: float, rng: random.Random, n: int = 3):
    if level <= 0:
        return [[(i, 100) for i in ids]]
    return [hypothesis(ids, conf, level * (1 + 0.5 * k), rng) for k in range(n)]
