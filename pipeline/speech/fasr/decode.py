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
MAX_HYPS, MAX_WORDS = 5, 16   # MAX_WORDS = voice_hyp_t slots per hypothesis (output only)
SEARCH_WORDS = 64             # words a hypothesis may hold during the search (a 10 s ramble is ~25-30)


def lse(a: float, b: float) -> float:
    if a < b:
        a, b = b, a
    return a if b <= NEG else a + math.log1p(math.exp(b - a))


@dataclass
class Trie:
    child: list[dict] = field(default_factory=lambda: [{}])
    words: list[list[int]] = field(default_factory=lambda: [[]])
    tok: list[int] = field(default_factory=lambda: [SEP])   # token that leads into each node
    cls: dict = field(default_factory=dict)                  # word id -> class byte (0 = other)
    name: dict = field(default_factory=dict)                 # word id -> spelling (for FILLER)

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
            t.cls[r["id"]] = int(r.get("class", 0) or 0); t.name[r["id"]] = r["word"]
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
    # "doesn't fit the word list" score: how much worse (nats per speech frame) the best
    # word-list path is than the best free phone path. ~0 when every sound fits a word;
    # grows for out-of-list words, mumbling and noise. reject_gap0 = gap where reject hits 50.
    reject_gap0: float = 0.35
    reject_gap_scale: float = 0.10
    # life-threat words: extra score per finished word (nats), and they veto the reject
    # (a missed "not breathing" costs far more than a false one, which the intake confirms).
    bias: dict = field(default_factory=dict)        # word id -> bonus
    life_veto_conf: int = 30                        # any life word this sure in the 5-best: reject <= 40


LIFE_WORDS = ["breathing", "breathe", "breath", "stopped", "not", "unconscious", "unresponsive", "choking",
              "choke", "bleeding", "blood", "spurting", "pumping", "gushing", "squirting", "pulsing",
              "cpr", "compressions", "pulse", "heart", "cardiac", "arrest", "chest", "epipen", "epinephrine",
              "anaphylactic", "anaphylaxis", "allergic", "swelling", "throat", "seizure", "drowning",
              "drowned", "tourniquet", "blue", "collapsed", "passed", "alive", "dying",
              # how bad the bleeding is: "bleeding" survives, the strength word gets garbled
              "lot", "won't", "stop", "soaked", "soaking", "through", "squirting", "everywhere",
              "pool", "puddle", "heavy", "badly", "faster"]


def life_bias(rows: list[dict], bonus: float = 1.0) -> dict:
    ids = {r["word"]: r["id"] for r in rows}
    return {ids[w]: bonus for w in LIFE_WORDS if w in ids}


def context_bias(rows: list[dict], words, bonus: float = 0.5, life: float = 1.0) -> dict:
    """Context-aware listening: a small extra score for the words the current tree or question
    expects (e.g. fang, swelling, rattlesnake on the bite pages), on top of the life-word bias.
    Unlike a word-list subset nothing is forbidden, so an unexpected answer still comes through.
    On the S3 this is one byte per lexicon id, swapped when the page changes."""
    ids = {r["word"]: r["id"] for r in rows}
    out = {ids[w]: bonus for w in words if w in ids}
    for k, v in life_bias(rows, life).items():
        out[k] = max(out.get(k, 0.0), v)
    return out


# live in every sector / form-field list, like the reserved commands (SKIP: router 0950954)
ALWAYS_WORDS = ["skip", "don't", "know", "dunno", "unsure", "not", "sure", "no", "time", "pass"]


def sector_trie(rows: list[dict], words, life: bool = True) -> "Trie":
    """Trie for a sector (or the union of the 2-3 sectors the router picked): its words + the
    reserved 0-9 + life words (life rules run on every sector). Used to re-decode the SAME turn-1
    logprobs (no second acoustic pass) and for the form page's per-sector lists."""
    keep = set(words) | set(ALWAYS_WORDS) | (set(LIFE_WORDS) if life else set())
    return Trie.from_lexicon([r for r in rows if r["id"] < 10 or r["word"] in keep])


def _finish(words: tuple, node: int, trie: Trie, p: Params):
    """Ways to close the word in progress: yields (new_words, score_delta)."""
    if node == 0:
        yield words, 0.0
    elif node == GARB:
        if words and words[-1] == 0:
            yield words, 0.0                  # <unk> <unk> = one <unk>: garbage runs don't eat slots
        elif len(words) < SEARCH_WORDS:
            yield words + (0,), 0.0
    else:
        if len(words) < SEARCH_WORDS:
            for w in trie.words[node]:
                yield words + (w,), p.word_bonus + p.bias.get(w, 0.0)


FILLER = {"the", "a", "an", "and", "is", "it", "it's", "its", "on", "in", "at", "of", "to", "his", "her", "my",
          "he", "she", "i", "i'm", "we", "they", "them", "him", "was", "were", "are", "be", "all", "so", "like",
          "um", "uh", "that", "this", "with", "just", "really", "very", "got", "get", "then", "there", "some", "kind"}


def fit_words(w: tuple, trie: "Trie", p: "Params") -> list[int]:
    """Squeeze a hypothesis into voice_hyp_t's MAX_WORDS slots, keeping what decides cards.
    Drop order (newest first within each step, so the opening of the sentence survives):
    <unk>; filler words (FILLER); other class-0 words that aren't life/biased words; then the
    latest words. Long sentences used to yield NO hypothesis at all (reject 100)."""
    w = list(w)
    if len(w) <= MAX_WORDS:
        return w
    keep = [x for x in w if x != 0] or [0]
    for droppable in (lambda x: trie.name.get(x) in FILLER,
                      lambda x: trie.cls.get(x, 0) == 0 and x >= 10 and x not in p.bias):
        i = len(keep) - 1
        while len(keep) > MAX_WORDS and i >= 0:
            if droppable(keep[i]) and keep[i] not in p.bias:
                del keep[i]
            i -= 1
    return keep[:MAX_WORDS]


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
    full: dict[tuple, tuple] = {}                    # fitted -> best untrimmed hypothesis
    for (words, node, last), (pb, pnb) in beams.items():
        for w2, d in _finish(words, node, trie, p):
            f2, sc = tuple(fit_words(w2, trie, p)), lse(pb, pnb) + d
            if f2 not in final or sc > final[f2]:
                full[f2] = w2
            final[f2] = lse(final.get(f2, NEG), sc)
    search.full = full                               # read by nbest (reject features need the untrimmed length)
    return sorted(final.items(), key=lambda kv: -kv[1])


def nbest(logp: np.ndarray, trie: Trie, p: Params = Params()) -> dict:
    """voice_hyp_t-shaped result. Its "reject" is the RAW fit-gap score: live code should call
    decode() (calibrated reject), which is canonical for the router and the C port."""
    res = search(logp, trie, p)
    if not res:
        return {"n": 0, "h": [], "reject": 100, "gap": 0.0, "junk": 1.0}
    top = max(s for _, s in res)
    post = np.array([math.exp(s - top) for _, s in res])
    post /= post.sum()
    junk = sum(q for (w, _), q in zip(res, post) if all(x == 0 for x in w))
    gap = fit_gap(logp, top)
    misfit = 1.0 / (1.0 + math.exp(-(gap - p.reject_gap0) / p.reject_gap_scale))
    hyps = []
    for (w, _), q in list(zip(res, post))[:MAX_HYPS]:
        conf = []
        for x in w:
            m = sum(q2 for (w2, _), q2 in zip(res, post) if x in w2)
            conf.append(int(round(100 * min(1.0, m))))
        hyps.append({"ids": list(w), "conf": conf, "p": round(float(q), 3)})
    reject = int(round(100 * max(junk, misfit)))
    full = list(getattr(search, "full", {}).get(res[0][0], res[0][0]))
    veto = False
    if p.bias and any(x in p.bias and c >= p.life_veto_conf for h in hyps for x, c in zip(h["ids"], h["conf"])):
        reject = min(reject, 40); veto = True
    return {"n": len(hyps), "h": hyps, "reject": reject, "veto": veto,
            "gap": round(gap, 3), "junk": round(float(junk), 3),
            # top hypothesis before it was trimmed to MAX_WORDS: the calibrated reject was fitted on
            # untrimmed lengths (words per frame, unk share), so a long clip must not look sparse
            "full_len": len(full), "full_unk": sum(x == 0 for x in full)}


def fit_gap(logp: np.ndarray, constrained: float) -> float:
    """Nats per non-blank frame between the free best path (any phone, any order) and the best
    word-list path. Cheap on the S3: one max per frame, no second search."""
    best = logp.max(axis=1)
    speech = int((logp.argmax(axis=1) != BLANK).sum())
    return max(0.0, float(best.sum() - constrained)) / max(1, speech)


# ---------------------------------------------------------------- calibrated reject
def reject_features(logp: np.ndarray, hyp: dict) -> list[float]:
    """Per-clip features for the calibrated reject (fasr calibrate). All cheap on the device:
    junk, unk share, 1 - mean conf, empty, speech-frame share, acoustic confidence, words per frame,
    and the word-list fit gap (nats per speech frame, from nbest)."""
    top = hyp["h"][0] if hyp.get("n") else {"ids": [], "conf": []}
    ids, conf = top["ids"], top["conf"]
    n_full = hyp.get("full_len", len(ids)); unk_full = hyp.get("full_unk", sum(i == 0 for i in ids))
    nb = logp.copy(); nb[:, BLANK] = NEG
    speech = np.exp(logp[:, BLANK]) < 0.5
    best = nb.max(1)
    return [float(hyp.get("junk", 1.0)),
            (unk_full / n_full) if n_full else 1.0,
            (1.0 - sum(conf) / (100.0 * len(conf))) if conf else 1.0,
            0.0 if ids else 1.0,
            float(speech.mean()) if len(speech) else 0.0,
            float(best[speech].mean()) if speech.any() else -10.0,
            n_full / (speech.sum() + 1.0),
            float(hyp.get("gap", 0.0))]


def calibrated_reject(logp: np.ndarray, hyp: dict, calib: dict) -> int:
    """0-100, where 50 is the calibrated threshold (reject at > 50). A life-threat word in the
    5-best (hyp["veto"], set by nbest) still caps it at 40."""
    f = np.array(reject_features(logp, hyp))
    z = ((f - np.array(calib["mean"])) / np.array(calib["std"])) @ np.array(calib["weights"]) + calib["bias"]
    t = calib["threshold"]; t = min(max(t, 1e-6), 1 - 1e-6)
    z -= math.log(t / (1 - t))
    r = int(round(100 / (1 + math.exp(-max(-50.0, min(50.0, z))))))
    return min(r, 40) if hyp.get("veto") else r


def decode(logp: np.ndarray, trie: Trie, p: Params = Params(), calib: dict | None = None) -> dict:
    """THE entry point for anything live (tether bridge, device reference, C port golden files).
    Canonical reject = calibrated_reject (runs/<run>/reject_calib.json, from `fasr calibrate`);
    the fit-gap sigmoid from nbest() is only one of its input features and is kept as reject_raw.
    Without a calibration file the raw score is the fallback (logged by the caller)."""
    hyp = nbest(logp, trie, p)
    hyp["reject_raw"] = hyp["reject"]
    if calib:
        hyp["reject"] = calibrated_reject(logp, hyp, calib)
    return hyp
