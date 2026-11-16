"""Word-list-constrained CTC beam search: phone posteriors -> n-best word-id sequences.

This is what makes the recognizer "first aid only": the search may only spell words that
are in lexicon.yaml. Anything else falls into a garbage word that outputs <unk> (id 0) at a
per-phone penalty, so off-topic speech turns into a high reject score instead of a wrong
first-aid word. Output matches the router's voice_hyp_t:
    {"n": k, "h": [{"ids": [...], "conf": [...]}, ...], "reject": 0-100}
Python reference; the firmware port (C) must give the same n-best on the golden files.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .phones import BLANK, SEP, TOK

NEG = -1e30
MAX_HYPS, MAX_WORDS = 5, 16


def lse(a: float, b: float) -> float:
    if a < b:
        a, b = b, a
    return a if b <= NEG else a + math.log1p(math.exp(b - a))


@dataclass
class Trie:
    child: list[dict] = field(default_factory=lambda: [{}])
    words: list[list[int]] = field(default_factory=lambda: [[]])
    tok: list[int] = field(default_factory=lambda: [SEP])   # token that leads into each node

    def add(self, phones: list[int], wid: int):
        n = 0
        for p in phones:
            nxt = self.child[n].get(p)
            if nxt is None:
                nxt = len(self.child)
                self.child[n][p] = nxt
                self.child.append({})
                self.words.append([])
                self.tok.append(p)
            n = nxt
        if wid not in self.words[n]:
            self.words[n].append(wid)

    @classmethod
    def from_lexicon(cls, rows: list[dict]) -> "Trie":
        t = cls()
        for r in rows:
            if r["id"] == 0:
                continue
            for pr in r.get("phones") or []:
                t.add([TOK[p] for p in pr.split()], r["id"])
        return t


GARB = -1   # node id of the garbage word


@dataclass
class Params:
    beam: int = 24
    tok_prune: float = -12.0      # ignore tokens with log-prob below this in a frame
    word_bonus: float = 0.5       # per finished real word (counters CTC's short bias)
    implicit_sep: float = -2.0    # starting a new word without the | token
    garbage_enter: float = -6.0   # starting an <unk> word
    garbage_phone: float = -1.5   # each phone inside an <unk> word


def _finish(words: tuple, node: int, trie: Trie, p: Params):
    """Ways to close the word in progress: yields (new_words, score_delta)."""
    if node == 0:
        yield words, 0.0
    elif node == GARB:
        if len(words) < MAX_WORDS:
            yield words + (0,), 0.0
    else:
        if len(words) < MAX_WORDS:
            for w in trie.words[node]:
                yield words + (w,), p.word_bonus


def search(logp: np.ndarray, trie: Trie, p: Params = Params()) -> list[tuple[tuple, float]]:
    """logp: (frames, tokens) log-probs. Returns [(word_ids, log_score)] best first."""
    # state key: (words, node, last_token) -> [p_blank, p_nonblank]
    beams = {((), 0, SEP): [0.0, NEG]}
    for t in range(logp.shape[0]):
        row = logp[t]
        cand = [int(c) for c in np.nonzero(row > p.tok_prune)[0] if c != BLANK]
        nxt: dict = {}

        def add(key, pb, pnb):
            e = nxt.get(key)
            if e is None:
                nxt[key] = [pb, pnb]
            else:
                e[0], e[1] = lse(e[0], pb), lse(e[1], pnb)

        for (words, node, last), (pb, pnb) in beams.items():
            tot = lse(pb, pnb)
            add((words, node, last), tot + row[BLANK], NEG)             # blank
            if last in cand and pnb > NEG:                                 # repeat collapses
                add((words, node, last), NEG, pnb + row[last])
            for c in cand:
                # a new token: allowed after blank always; after a different token always
                base = lse(pb, pnb) if c != last else pb
                if base <= NEG:
                    continue
                s = base + row[c]
                if c == SEP:
                    if node == 0:
                        continue                                           # || adds nothing
                    for w2, d in _finish(words, node, trie, p):
                        add((w2, 0, SEP), NEG, s + d)
                    continue
                if node == GARB:
                    add((words, GARB, c), NEG, s + p.garbage_phone)
                    continue
                ch = trie.child[node].get(c)
                if ch is not None:
                    add((words, ch, c), NEG, s)
                if node == 0:
                    add((words, GARB, c), NEG, s + p.garbage_enter + p.garbage_phone)
                else:                                                      # implicit boundary
                    ch0 = trie.child[0].get(c)
                    if ch0 is not None:
                        for w2, d in _finish(words, node, trie, p):
                            add((w2, ch0, c), NEG, s + d + p.implicit_sep)
        beams = dict(sorted(nxt.items(), key=lambda kv: -lse(*kv[1]))[: p.beam])
    final: dict[tuple, float] = {}
    for (words, node, last), (pb, pnb) in beams.items():
        for w2, d in _finish(words, node, trie, p):
            final[w2] = lse(final.get(w2, NEG), lse(pb, pnb) + d)
    return sorted(final.items(), key=lambda kv: -kv[1])


def nbest(logp: np.ndarray, trie: Trie, p: Params = Params()) -> dict:
    """voice_hyp_t-shaped result."""
    res = search(logp, trie, p)
    if not res:
        return {"n": 0, "h": [], "reject": 100}
    top = max(s for _, s in res)
    post = np.array([math.exp(s - top) for _, s in res])
    post /= post.sum()
    junk = sum(q for (w, _), q in zip(res, post) if all(x == 0 for x in w))
    hyps = []
    for (w, _), q in list(zip(res, post))[:MAX_HYPS]:
        conf = []
        for x in w:
            m = sum(q2 for (w2, _), q2 in zip(res, post) if x in w2)
            conf.append(int(round(100 * min(1.0, m))))
        hyps.append({"ids": list(w), "conf": conf, "p": round(float(q), 3)})
    return {"n": len(hyps), "h": hyps, "reject": int(round(100 * junk))}
