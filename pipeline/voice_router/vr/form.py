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

from .intake import (_fill, _questions, answer_turn, fill_nbest, tiles, tokenize, walk, _FILLER)

SURE = 0.80            # router probability above which the other trees are dropped
MAX_TREES = 3


@dataclass
class Candidate:
    """One tree the router still believes in, with its own slot map and progress."""
    name: str
    tree: dict
    smap: dict
    p: float = 1.0
    filled: dict = field(default_factory=dict)
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


def first_sentence(hyps: list[str] | str, cands: list[Candidate]):
    """Turn 1. Returns ("open", tree name, card) when a life rule or a full set of blanks lands on
    a card, else ("form", None, None) and the candidates hold their progress."""
    hyps = [hyps] if isinstance(hyps, str) else hyps
    cands.sort(key=lambda c: -c.p)
    for c in cands:
        c.filled = fill_nbest(hyps, c.smap)
        c.advance()
        if c.card not in c.tree["nodes"]:             # a life rule jumped to another tree's card
            return "open", c.name, c.card
    for c in cands:                                   # life rule inside the tree: same, open it
        if c.card in _life_cards(c) and not c.asking:
            return "open", c.name, c.card
    if cands and (cands[0].p >= SURE or len(cands) == 1):
        del cands[1:]
    if len(cands) == 1 and not cands[0].asking and cands[0].card != cands[0].tree["root"]:
        return "open", cands[0].name, cands[0].card
    return "form", None, None


def _life_cards(c: Candidate) -> set:
    lt = c.smap.get("life_threat") or []
    if isinstance(lt, dict):
        return {card for m in lt.values() for card in m.values()}
    return {r["go"] for r in lt}


def _lookahead(c: Candidate, limit: int = 4) -> list[tuple[str, str]]:
    """(card, slot) blanks this tree will ask whatever the current one is answered with:
    the current blank, then any blank every answer leads to next."""
    out, card, asking, filled = [], c.card, c.asking, dict(c.filled)
    while asking and len(out) < limit:
        out.append((card, asking))
        nxt = set()
        for t in tiles(c.tree, c.smap, card):
            if not t.get("value"):
                nxt.add((None, None))
                continue
            n_card, _, n_ask = walk(c.tree, c.smap, {**filled, asking: t["value"]}, start=card)
            nxt.add((n_card, n_ask) if n_ask else (None, None))
        if len(nxt) != 1 or (None, None) in nxt:
            break
        card, asking = nxt.pop()
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
        for card, slot in _lookahead(c):
            if slot in seen:
                seen[slot].trees.append(c.name)
                continue
            f = Field(slot, c.tree["nodes"][card].get("label", slot), tiles(c.tree, c.smap, card), [c.name])
            seen[slot] = f
            rows.append(f)
    rows.sort(key=lambda f: f.key not in life)        # stable: life blanks first, tree order kept
    return fields + rows


def answer(text: str, cands: list[Candidate], fields: list[Field] | None = None):
    """One spoken (or tapped: "number 2") answer to the screen. Each open field reads the words
    with its own short answer list, in screen order; follow-up fields that appear are read from the
    same words. Returns ("open", tree, card) or ("form", None, None) with fewer fields left."""
    fields = fields if fields is not None else open_fields(cands)
    words = set(tokenize(text))
    if fields and fields[0].key == "@topic":
        hit = [c for c in cands if _named(c, words)]
        if len(hit) == 1:
            cands[:] = hit
    progressed = True
    while progressed:
        progressed = False
        for c in cands:
            if not c.asking:
                continue
            filled, pick, _ = answer_turn(text, c.tree, c.smap, c.card, c.asking, c.filled)
            if pick and pick.get("value"):
                c.filled = filled
                c.advance(start=c.card)
                progressed = True
    done = [c for c in cands if not c.asking]
    if len(cands) == 1 and done:
        return "open", done[0].name, done[0].card
    return "form", None, None


def _named(c: Candidate, words: set) -> bool:
    title = set(tokenize(c.tree.get("title", c.name))) - _FILLER
    return bool(title) and title <= words


# ---- scoring: how many spoken turns to the right card with this screen --------------------------

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
        k = next((i for i, t in enumerate(ts) if t["to"] in toward), None)
        if k is None:
            return None
        if asking and ts[k].get("value"):
            said.append(ts[k].get("label") or ts[k]["value"])
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
