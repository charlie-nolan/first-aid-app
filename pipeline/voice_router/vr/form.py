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
        c.filled = fill_nbest(hyps, c.smap)
        c.advance()
        if c.card not in c.tree["nodes"]:             # a life rule jumped to another tree's card
            return "open", c.name, c.card
    for c in cands:                                   # emergency card inside the tree: open it now (Charlie:
        go = _rule_hit(c)                             # one-shot, general info first); its own blank ("very
        if go:                                        # cold or from water?") only shows on the open card
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
            f = Field(slot, c.tree["nodes"][card].get("label", slot), tiles(c.tree, c.smap, card), [c.name], when)
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
            lab = ts[k].get("label") or ""
            group = (" or " in lab or "," in lab or not lab) and ts[k]["value"] not in ("yes", "no")
            said.append(ts[k]["value"].replace("_", " ") if group else lab)   # "Sunburn or snow blindness": say
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
