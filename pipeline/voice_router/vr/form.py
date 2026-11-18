"""Fill-in-the-blanks intake (Charlie 11-17): no more one-question pages.

1. The first sentence goes through the recognizer (n-best words).
2. The router keeps the 2-3 likely trees (the experts), not one.
3. Every candidate tree is searched for its keywords at once; life-threat rules of every
   candidate are checked first and open their card straight away.
4. ONE screen lists every blank still open: a "which is it" field when the trees still compete,
   then each tree's open blanks. Blanks shared by several trees (breathing, body part...) show once.
   Each field has its own short answer list; an answer fills only its own field. Fields that only
   matter after an answer (which bone, once "arm" is known) appear on the same screen when the
   answer lands.
5. When the blanks lead to one card, that cited card opens. Nothing is generated.

The trees stay the source of every card and cite; their question cards become fields.

    from vr.form import Candidate, first_sentence, open_fields, answer
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .intake import (_find, _stem, _fill, fill, _questions, answer_turn, fill_nbest, tiles, tokenize, walk, _FILLER)

SURE = 0.80            # router probability above which the other trees are dropped
MAX_TREES = 3
MAX_TABS = 5           # the device's windows (Charlie 11-04 plan; window L/R switches)
AUTO_TABS = False      # split without being asked (off: Charlie 11-17 picked the button combo instead)


@dataclass
class Candidate:
    """One tree the router still believes in, with its own slot map and progress."""
    name: str
    tree: dict
    smap: dict
    p: float = 1.0
    filled: dict = field(default_factory=dict)
    heard: str = ""                  # the first sentence as heard (for ranking partial evidence)
    hyps: list | None = None         # this tree's own n-best: the same utterance re-decoded with its
                                     # sector's words (speech side, 11-17); None = the shared n-best
    card: str | None = None          # where this tree's walk stands now
    asking: str | None = None        # the blank that card asks (None = a plain card: open it)

    def advance(self, start: str | None = None):
        self.card, _, self.asking = walk(self.tree, self.smap, self.filled, start=start)
        return self


@dataclass
class Field:
    key: str                          # slot name, or "@topic" for the which-is-it field
    question: str                     # the question card's own label
    choices: list                     # [{label, to, value}] as tiles() gives them, or trees for @topic
    trees: list                       # candidate names this field belongs to
    when: tuple | None = None         # (slot, value) this field depends on; None = asked now


def first_sentence(hyps: list[str] | str, cands: list[Candidate], multi: bool = False):
    """Turn 1. Returns ("open", tree name, card) when a life rule or a full set of blanks lands on
    a card; ("tabs", None, None) when separate problems were named (split_tabs(cands) gives one
    candidate group per tab, each with its own form); else ("form", None, None).
    multi=True when the person held TALK + WIN_R (Charlie 11-17: "multiple injuries"); plain TALK
    is one problem, one tab."""
    hyps = [hyps] if isinstance(hyps, str) else hyps
    cands.sort(key=lambda c: -c.p)
    for c in cands:
        c.filled = fill_nbest(c.hyps or hyps, c.smap)
        c.heard = (c.hyps or hyps)[0]
        c.advance()
    order = sorted(cands, key=lambda c: (-round(c.p, 6), not _rule_hit(c)))   # a tie: own rule first
    for c in order:                                   # in rank order, each tree's own emergency first
        if c.card not in c.tree["nodes"]:             # (trees 11-18: asthma's jump to allergy/severe beat
            return "open", c.name, c.card             # the top-ranked choking tree's own rule): a life
        go = _rule_hit(c)                             # rule that jumped to another tree's card, or an
        if go:                                        # emergency card inside the tree (Charlie: one-shot,
            if go in c.tree["nodes"]:                 # general info first); its own blank shows on the card
                q = _questions(c.tree, c.smap).get(go)
                c.card, c.asking = go, (q["slot"] if q else None)
            return "open", c.name, c.card
    if (multi or AUTO_TABS) and len(split_tabs(cands)) > 1:
        return "tabs", None, None                     # two problems named: one tab each
    if cands and (cands[0].p >= SURE or len(cands) == 1):
        del cands[1:]
    if len(cands) == 1 and not cands[0].asking and cands[0].card != cands[0].tree["root"] \
            and cands[0].card not in _life_cards(cands[0]):    # "breathing fine" must not open a CPR-tree card
        return "open", cands[0].name, cands[0].card
    return "form", None, None


def _own_evidence(c: Candidate, others: list[Candidate]) -> set:
    """Blanks this tree filled that no other candidate tree even has: words only it explains."""
    theirs = set().union(*(set(o.smap.get("slots", {})) for o in others)) if others else set()
    return {s for s in c.filled if s not in theirs}


def split_tabs(cands: list[Candidate]) -> list[list[Candidate]]:
    """Charlie 11-17: when the first sentence names separate problems ("his arm is bleeding and
    I think his leg is broken"), each opens in its own tab (window) with its own form. A tree
    gets its own tab when it filled a blank no other candidate tree has; candidates without
    such evidence stay in the first tab (where a which-is-it field sorts them)."""
    own = [c for c in cands if _own_evidence(c, [o for o in cands if o is not c])]
    if len(own) < 2:
        return [cands]
    rest = [c for c in cands if c not in own]
    tabs = [[c] for c in own[:MAX_TABS]]
    tabs[0] += rest
    return tabs


def _rule_hit(c: Candidate) -> str | None:
    """The card of the first life rule the filled blanks trigger (reaching a life card by simply
    answering the root, e.g. "breathing fine" in the CPR tree, does not count)."""
    lt = c.smap.get("life_threat") or []
    if isinstance(lt, dict):
        lt = [{"if": {s: v}, "go": card} for s, m in lt.items() for v, card in m.items()]
    for r in lt:
        if all(c.filled.get(s) == v for s, v in r["if"].items()) and \
                not any(c.filled.get(s) == v for s, v in (r.get("unless") or {}).items()):
            return r["go"]
    return None


def _life_cards(c: Candidate) -> set:
    lt = c.smap.get("life_threat") or []
    if isinstance(lt, dict):
        return {card for m in lt.values() for card in m.values()}
    return {r["go"] for r in lt}


def _lookahead(c: Candidate, limit: int = 6) -> list[tuple[str, str, tuple | None]]:
    """(card, slot, when) for every blank still ahead, breadth first: the current blank (when
    None), then the blank each of its answers leads to (when = (slot, value)), and so on. The
    screen shows the conditional ones under their parent and greys out the branches not taken."""
    out, seen = [], set()
    todo = [(c.card, c.asking, dict(c.filled), None)]
    while todo and len(out) < limit:
        card, asking, filled, when = todo.pop(0)
        if not asking or asking in seen:
            continue
        seen.add(asking)
        out.append((card, asking, when))
        for t in tiles(c.tree, c.smap, card):
            if not t.get("value"):
                continue
            f2 = {**filled, asking: t["value"]}
            n_card, _, n_ask = walk(c.tree, c.smap, f2, start=card)
            if n_ask:
                todo.append((n_card, n_ask, f2, (asking, t["value"])))
    return out


def open_fields(cands: list[Candidate]) -> list[Field]:
    """What the screen shows now. Order: which-is-it (if trees compete), life-threat blanks,
    then the rest in tree order. Shared slot names appear once."""
    fields, seen = [], {}
    if len(cands) > 1:
        fields.append(Field("@topic", "Which is it?",
                            [{"label": c.tree.get("title", c.name), "to": c.name, "value": c.name} for c in cands],
                            [c.name for c in cands]))
    life = set()
    for c in cands:
        lt = c.smap.get("life_threat") or []
        life |= set(lt) if isinstance(lt, dict) else {s for r in lt for s in r.get("if", {})}
    rows = []
    for c in cands:
        for card, slot, when in _lookahead(c):
            if slot in seen:
                seen[slot].trees.append(c.name)
                continue
            ts = tiles(c.tree, c.smap, card)
            if when:                                  # not asked yet: answers only, no menu tiles
                ts = [t for t in ts if t.get("value")]
            f = Field(slot, c.tree["nodes"][card].get("label", slot), ts, [c.name], when)
            seen[slot] = f
            rows.append(f)
    top = most_telling(cands)                         # stable: life blanks, then the most telling
    rows.sort(key=lambda f: (f.key not in life, f.key != top))   # blank, then tree order
    return fields + rows


def answer(text: str, cands: list[Candidate], fields: list[Field] | None = None):
    """One spoken (or tapped: "number 2") answer to the screen. Each open field reads the words
    with its own short answer list, in screen order; follow-up fields that appear are read from the
    same words. Returns ("open", tree, card), ("skip", None, [(tree, card)...]) when the person says
    "skip" / "I don't know", or ("form", None, None) with fewer fields left."""
    fields = fields if fields is not None else open_fields(cands)
    words = set(tokenize(text))
    if words & {"skip", "dunno"} or {"don't", "know"} <= words or {"no", "time"} <= words:
        return "skip", None, skip(cands)           # same as the SKIP buttons (UP+DOWN together)
    if fields and fields[0].key == "@topic":
        hit = [c for c in cands if _named(c, tokenize(text))]
        if len(hit) == 1:
            cands[:] = hit
    bare_yn = len(words) <= 3 and words and not words - {"yes", "yeah", "yep", "no", "nope", "nah"}
    fired = {c.name: _rule_hit(c) for c in cands}   # rules already met before this answer
    progressed, states, passes = True, set(), 0     # tiles can loop back to a page already
    while progressed and passes < 20:               # answered in this call: never re-ask it
        progressed, passes = False, passes + 1
        for c in cands:
            if not c.asking or (c.name, c.card, c.asking) in states:
                continue
            states.add((c.name, c.card, c.asking))
            filled, pick, _ = answer_turn(text, c.tree, c.smap, c.card, c.asking, c.filled)
            extra = {k: v for k, v in filled.items() if k not in c.filled and k != c.asking}
            if extra:                                  # other blanks heard in the same breath stay
                c.filled = {**c.filled, **extra}       # ("he's unresponsive now" on any field)
                go = _rule_hit(c)                      # life first, as on the first sentence
                if go and go != fired[c.name]:
                    return "open", c.name, go
            if not (pick and pick.get("value")):
                v = snap(text, c)                      # madlibs: a near word snaps to its answer
                if v:
                    filled, pick = {**c.filled, c.asking: v}, {"value": v}
            if pick and pick.get("value"):
                c.filled = {**filled, **c.filled, c.asking: filled.get(c.asking, pick["value"])}
                go = _rule_hit(c)
                if go and go != fired[c.name]:
                    return "open", c.name, go
                _step(c, pick if pick.get("to") else None)
                progressed = True
                if bare_yn:                            # a bare "no" answers one field, not the next
                    progressed = False                 # one too ("out of place? no" is not "no splint")
                    break
    done = [c for c in cands if not c.asking]
    if len(cands) == 1 and done:
        return "open", done[0].name, done[0].card
    return "form", None, None


def _step(c: Candidate, tile: dict | None):
    """Advance after an answer. If the walk stays on the same card asking the same blank (the slot
    map's question lists the value under another name or a card that moved), follow the tapped or
    matched tile's own target instead: an answer always moves the form on."""
    was = (c.card, c.asking)
    c.advance(start=c.card)
    if (c.card, c.asking) == was and tile and tile.get("to"):
        if tile["to"] in c.tree["nodes"]:
            c.advance(start=tile["to"])
        else:                                       # another tree's card ("improvised_gear/splint")
            c.card, c.asking = tile["to"], None


def node_id(name: str, card: str, tree: dict) -> str:
    """The board's id for a card: "tree/card"; a card in another tree keeps its own id."""
    return card if card not in tree["nodes"] and "/" in card else f"{name}/{card}"


def fuzz(trees: dict, sentences: list[str], depth: int = 10) -> list[str]:
    """Tap the first, then the last choice of the top field, up to `depth` taps, for each sentence
    and every tree that keeps it. Returns the loops found ("tree: sentence -> card asking slot"):
    a tap that leaves the form exactly as it was. Run on the real trees after any slot change."""
    bad = []
    for text in sentences:
        for end in (0, -1):
            for name, (tree, smap) in trees.items():
                cands = [Candidate(name, tree, smap)]
                kind, _, _ = first_sentence(text, cands)
                for _ in range(depth):
                    if kind != "form":
                        break
                    fs = open_fields(cands)
                    if not fs or not fs[0].choices:
                        break
                    ch = fs[0].choices[end]
                    before = (cands[0].card, cands[0].asking, dict(cands[0].filled))
                    kind, _, _ = pick(fs[0].key, ch.get("value"), cands, to=ch.get("to"))
                    if kind == "form" and (cands[0].card, cands[0].asking, cands[0].filled) == before:
                        bad.append(f"{name}: {text!r} -> {cands[0].card} asking {cands[0].asking} "
                                   f"(tap {ch.get('label')!r})")
                        break
    return bad


def pick(key: str, value: str | None, cands: list[Candidate], to: str | None = None):
    """A tapped answer (buttons on the form): fills that one field exactly, no word matching.
    key "@topic" keeps only the tree named by value. A plain tile (no value: a menu choice) gives
    `to`, its target card: the tree asking that field goes there. Same returns as answer()."""
    if value is None and to:
        for c in cands:
            if c.asking != key:
                continue
            if to.startswith(c.name + "/"):         # a full node id "tree/card" from the board
                to = to[len(c.name) + 1:]
            if to in c.tree["nodes"]:
                c.advance(start=to)
            elif "/" in to:                         # a plain tile into another tree ("Start CPR")
                return "open", c.name, to
    elif key == "@topic":
        cands[:] = [c for c in cands if c.name == value] or cands
    else:
        for c in cands:
            if key in c.smap.get("slots", {}) or c.asking == key:
                c.filled = {**c.filled, key: value}
                if c.asking:
                    tile = next((t for t in tiles(c.tree, c.smap, c.card) if t.get("value") == value), None)
                    _step(c, tile)
    done = [c for c in cands if not c.asking]
    if len(cands) == 1 and done:
        return "open", done[0].name, done[0].card
    return "form", None, None


def _soft(c: Candidate, slot: str, value: str, label: str) -> float:
    """Partial evidence for an answer the person did not quite say: the share of a phrase's words
    (or the tile label's) that were heard. Below fill strength by design: it only ranks."""
    heard = {_stem(w) for w in tokenize(c.heard or "")} - _FILLER
    best = 0.0
    for ph in list(c.smap.get("slots", {}).get(slot, {}).get(value, [])) + [label]:
        pw = {_stem(w) for w in tokenize(ph)} - _FILLER
        if pw:
            best = max(best, len(pw & heard) / len(pw))
    return best


def ranked_cards(cands: list[Candidate], limit: int = 12) -> list[tuple[str, str]]:
    """Every card the filled blanks still allow, best first (Charlie 11-17: "even if 60% were
    filled it'd show up in top 3"). Emergency cards first; then by the partial evidence for the
    answers each card assumes (words of that answer heard but not enough to fill it), minus a
    little per assumed blank; then router belief; then the tree's own tile order."""
    scored, seen, order = {}, set(), 0
    for c in cands:
        todo = [(c.card, c.asking, dict(c.filled), 0.0, 0)]
        visited, steps = set(), 0           # tile graphs can loop back (menus, "start over")
        while todo and len(seen) < limit * 4 and steps < 2000:
            card, asking, filled, ev, n = todo.pop(0)
            state = (card, asking, tuple(sorted(filled.items())))
            if state in visited:
                continue
            visited.add(state); steps += 1
            if not asking:
                key = (c.name, card)
                if key not in seen:
                    seen.add(key)
                    scored[key] = (ev - 0.1 * n + 0.5 * c.p, order); order += 1
                continue
            for t in tiles(c.tree, c.smap, card):
                if t.get("value"):
                    f2 = {**filled, asking: t["value"]}
                    n_card, _, n_ask = walk(c.tree, c.smap, f2, start=card)
                    todo.append((n_card, n_ask, f2, ev + _soft(c, asking, t["value"], t.get("label", "")), n + 1))
                elif t.get("to") in c.tree["nodes"]:
                    todo.append((t["to"], None, filled, ev, n))
    life = {(c.name, k) for c in cands for k in _life_cards(c)}
    out = sorted(scored, key=lambda k: (k not in life, -scored[k][0], scored[k][1]))
    return out[:limit]


def skip(cands: list[Candidate], limit: int = 12) -> list[tuple[str, str]]:
    """Charlie 11-17: a SKIP on the form ("don't know / no time") jumps straight to the cards the
    blanks filled so far still allow, best first (ranked_cards), to cycle through with the buttons."""
    return ranked_cards(cands, limit)


def most_telling(cands: list[Candidate]) -> str | None:
    """The open blank whose answer splits the still-possible cards most evenly: the one to put at
    the top of the form (the others follow)."""
    import math
    best, best_h = None, -1.0
    for c in cands:
        if not c.asking:
            continue
        counts = []
        for t in tiles(c.tree, c.smap, c.card):
            if t.get("value"):
                sub = Candidate(c.name, c.tree, c.smap, c.p, {**c.filled, c.asking: t["value"]}, heard=c.heard)
                sub.advance(start=c.card)
                counts.append(max(1, len(ranked_cards([sub], 40))))
        tot = sum(counts)
        h = -sum(n / tot * math.log(n / tot) for n in counts) if tot else 0.0
        if h > best_h:
            best, best_h = c.asking, h
    return best


SNAP_MIN, SNAP_MARGIN = 0.80, 0.10


def snap(text: str, c: Candidate) -> str | None:
    """Charlie 11-17 ("like madlibs"): each blank takes only its own few answers, so a word that is
    close to one of them (misheard "ankel", "swolen") snaps to that answer. Only when one answer is
    clearly closest, only words of 4+ letters, and never on a life-threat blank (there a near miss
    must ask, not guess)."""
    from difflib import SequenceMatcher
    if not c.asking or c.asking in _life_slots(c):
        return None
    card_q = _questions(c.tree, c.smap).get(c.card, {})
    best: dict[str, float] = {}
    from .intake import HEDGE, NEGATION, _scope
    raw = tokenize(text)                               # same guard as fill(): a negated or hedged word
    heard = [w for i, w in enumerate(raw) if len(w) >= 4 and w not in _FILLER   # never snaps
             and not any(x in NEGATION for x in _scope(raw, i, True))           # ("not on the face")
             and not any(x in HEDGE for x in _scope(raw, i, False))]
    for t in tiles(c.tree, c.smap, c.card):
        v = t.get("value")
        if not v:
            continue
        cands = set(tokenize(t.get("label", ""))) | {
            w for ph in c.smap.get("slots", {}).get(c.asking, {}).get(v, []) for w in tokenize(ph)}
        cands = {w for w in cands if len(w) >= 4 and w not in _FILLER}
        for h in heard:
            for w in cands:
                r = SequenceMatcher(None, h, w).ratio()
                if r > best.get(v, 0):
                    best[v] = r
    if not best:
        return None
    ranked = sorted(best.items(), key=lambda kv: -kv[1])
    top, score = ranked[0]
    if score < SNAP_MIN or (len(ranked) > 1 and score - ranked[1][1] < SNAP_MARGIN):
        return None
    return top if card_q.get("choices", {}).get(top) or any(t.get("value") == top for t in tiles(c.tree, c.smap, c.card)) else None


def _life_slots(c: Candidate) -> set:
    lt = c.smap.get("life_threat") or []
    return set(lt) if isinstance(lt, dict) else {s for r in lt for s in r.get("if", {})}


# Tier-1 topic words name a CONDITION, never a body area, object or common adjective (PC check
# 11-18 of 40f375f: titles gave "back", "chest", "cold", "tent", "broken", "wound", "bit"... and
# flipped 52 scenes to the wrong tree). Source: smap topic_words (phrases, curated with the
# trees), else the tree's file name when it is itself a condition name, plus these defaults.
_NAME_IS_TOPIC = {"choking", "hypothermia", "frostbite", "drowning", "lightning", "nosebleed",
                  "seizures", "stroke", "asthma", "allergy", "dehydration", "diabetic", "fainting",
                  "altitude", "fractures", "sprains", "cpr"}
_DEFAULT_TOPICS = {"wfa-co": ["carbon monoxide"], "wfa-allergy": ["anaphylaxis", "anaphylactic",
                   "allergic reaction"], "wfa-poison-ivy": ["poison ivy", "poison oak", "sumac"],
                   "heat": ["heat stroke", "heatstroke", "heat exhaustion"],
                   "wfa-choking": ["choking", "choke", "choked"], "wfa-seizures": ["seizure", "seizing"],
                   "wfa-cpr": ["cpr"], "fractures": ["fracture", "fractured"],
                   "wfa-diabetic": ["diabetic", "diabetes"], "wfa-altitude": ["altitude sickness"]}


def _key(w: str) -> str:
    k = _stem(w)
    return k[:-1] if len(k) >= 5 and k.endswith("e") else k   # choke/choking/choked -> chok; be != bee


def topic_phrases(name: str, tree: dict, smap: dict) -> list[tuple]:
    """Condition names for the tree, each a tuple of keyed words ("carbon", "monoxid")."""
    base = name.split("wfa-")[-1]
    raw = list(smap.get("topic_words") or []) + _DEFAULT_TOPICS.get(name, []) + \
        ([base] if base in _NAME_IS_TOPIC else [])
    return [t for t in {tuple(_key(w) for w in tokenize(ph)) for ph in raw} if t]


def topic_words(name: str, tree: dict, smap: dict) -> set:
    return {w for t in topic_phrases(name, tree, smap) for w in t}


def name_spans(words: list[str], name: str, tree: dict, smap: dict) -> list[tuple[int, int]]:
    """Where in the words each of the tree's topic phrases is heard, as (start, end) spans."""
    keys, out = [_key(w) for w in words], []
    for t in topic_phrases(name, tree, smap):
        pos = _find(keys, list(t), gap=0)
        if pos is not None:
            out.append((pos[0], pos[-1] + 1))
    return out


def named_trees(words: list[str], trees: dict) -> set:
    """Trees named outright. A name heard only inside another tree's longer name does not count:
    "heat stroke" names heat, not stroke (trees thread 11-18)."""
    spans = {n: name_spans(words, n, t, m) for n, (t, m) in trees.items()}
    every = [(sp, n) for n, ss in spans.items() for sp in ss]
    def inside(sp, n):
        return any(o != n and a <= sp[0] and sp[1] <= b and (b - a) > (sp[1] - sp[0]) for (a, b), o in every)
    return {n for n, ss in spans.items() if any(not inside(sp, n) for sp in ss)}


def names_tree(words: list[str], name: str, tree: dict, smap: dict) -> bool:
    """Do these words name the tree's condition (every word of one of its topic phrases, in order)?"""
    keys = [_key(w) for w in words]
    return any(_find(keys, list(t), gap=0) is not None for t in topic_phrases(name, tree, smap))


def _named(c: Candidate, words: list[str]) -> bool:
    """The answer names this tree on the which-is-it field: its tile label (every title word) or
    one of its condition names (s010: "carbon monoxide poisoning" for "Carbon monoxide")."""
    title = set(tokenize(c.tree.get("title", c.name))) - _FILLER
    return (bool(title) and title <= set(words)) or names_tree(words, c.name, c.tree, c.smap)


# ---- scoring: how many spoken turns to the right card with this screen --------------------------

def _say(c: Candidate, slot: str, value: str) -> str:
    """What a person would say for a grouped tile: the first of the value's own phrases that
    fills it on its own (11-18: the raw value name "neck back" filled nothing and looped)."""
    slots = c.smap.get("slots", {})
    vals = slots.get(slot, {})
    ok = [ph for ph in vals.get(value, [])
          if fill(ph, {slot: vals}, c.smap.get("priority")).get(slot) == value]
    clean = [ph for ph in ok if not set(fill(ph, slots, c.smap.get("priority"))) - {slot}]
    return (clean or ok or [value.replace("_", " ")])[0]   # prefer one no other blank also hears


def _path_labels(c: Candidate, ok: set, anc: dict, most: int = 8):
    """The right tile's words for each open blank on the way to the goal, in order, plus how
    many plain (no-blank) cards must be tapped through. None if the walk is off the path."""
    filled, card, asking = dict(c.filled), c.card, c.asking
    toward = set().union(*(anc.get(g, {g}) for g in ok))
    said, taps = [], 0
    for _ in range(most):
        if card in ok:
            return said, taps
        if card not in toward:
            return None
        ts = tiles(c.tree, c.smap, card)
        life = _life_cards(c) - ok
        order = [i for i, t in enumerate(ts) if t["to"] in ok] + \
                [i for i, t in enumerate(ts) if t["to"] in toward and t["to"] not in life] + \
                [i for i, t in enumerate(ts) if t["to"] in toward]
        k = order[0] if order else None
        if k is None:
            return None
        if asking and ts[k].get("value"):
            lab = ts[k].get("label") or ""
            group = (" or " in lab or "," in lab or not lab) and ts[k]["value"] not in ("yes", "no")
            said.append(_say(c, asking, ts[k]["value"]) if group else lab)   # "Sunburn or snow blindness": say
                                                                               # the one that fits; yes/no: the label
            filled = {**filled, asking: ts[k]["value"]}
            card, _, asking = walk(c.tree, c.smap, filled, start=card)
        else:
            taps += 1
            card, _, asking = walk(c.tree, c.smap, filled, start=ts[k]["to"])
    return None


def form_turns(name: str, tree: dict, smap: dict, line: str, ok: set, anc: dict) -> int | None:
    """Single-tree score (gold tree known): 1 if the first sentence opens the card; else the
    person answers the form once with the right tile words for every open blank (one breath);
    each blank the form could not read from that costs one more turn (saying just that answer);
    each plain card on the way is one tap. None = wrong card or off the path."""
    c = Candidate(name, tree, smap)
    cands = [c]
    kind, _, card = first_sentence(line, cands)
    if kind == "open" or not c.asking:
        card = card or c.card
        if card in ok:
            return 1
    plan = _path_labels(c, ok, anc)
    if plan is None:
        return None
    said, _ = plan
    turn = 1
    if said:
        turn += 1
        answer(" . ".join(said), cands)
    for _ in range(8):
        if c.card in ok:
            return turn
        plan = _path_labels(c, ok, anc)
        if plan is None:
            return None
        said, _ = plan
        turn += 1
        if c.asking and said:
            before = (c.card, c.asking)
            answer(said[0], cands)
            if (c.card, c.asking) == before:
                return None
        else:                                          # plain card on the way: one tap
            ts = tiles(c.tree, c.smap, c.card)
            toward = set().union(*(anc.get(g, {g}) for g in ok))
            nxt = next((t["to"] for t in ts if t["to"] in toward), None)
            if nxt is None:
                return None
            c.advance(start=nxt)
    return None


def first_rank(name: str, tree: dict, smap: dict, line: str, ok: set, anc: dict):
    """After the first sentence only: (rank of the right card in ranked_cards or None,
    share of the blanks on its path that the sentence filled)."""
    c = Candidate(name, tree, smap)
    kind, _, card = first_sentence(line, [c])
    full = _path_labels(Candidate(name, tree, smap).advance(), ok, anc)
    left = _path_labels(c, ok, anc) if c.card in tree["nodes"] else None
    need = len(full[0]) if full else 0
    share = 1.0 if not need else 1 - (len(left[0]) if left else need) / need
    if kind == "open":
        return (0 if card in ok else None), share
    if c.card in ok:                        # stands on the gold card (a question card with text)
        return 0, max(0.0, share)
    ranks = [i for i, (_, k) in enumerate(ranked_cards([c], 40)) if k in ok]
    return (ranks[0] if ranks else None), max(0.0, share)


def second_rank(name: str, tree: dict, smap: dict, hyps, ok: set, anc: dict):
    """After the first sentence (hyps: text or n-best) plus ONE answer: the right tile for the
    form's top blank (most_telling) on the way to the gold card. Returns the right card's rank in
    ranked_cards (0 = it opened / is first) or None."""
    c = Candidate(name, tree, smap)
    kind, _, card = first_sentence(hyps, [c])
    if kind == "open":
        return 0 if card in ok else None
    top = most_telling([c])
    toward = set().union(*(anc.get(g, {g}) for g in ok))
    if top and c.asking == top:
        ts = tiles(tree, smap, c.card)
        t = next((t for t in ts if t["to"] in toward and t.get("value")), None)
        if t:
            c.filled = {**c.filled, top: t["value"]}
            c.advance(start=c.card)
    if c.card in ok:                        # same as first_rank: standing on the gold card
        return 0
    ranks = [i for i, (_, k) in enumerate(ranked_cards([c], 40)) if k in ok]
    return ranks[0] if ranks else None
