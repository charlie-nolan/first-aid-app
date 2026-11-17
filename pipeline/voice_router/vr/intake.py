"""Slot-filling intake (Charlie 11-16): the first sentence fills in as many blanks as it can,
then the tree skips every question already answered and asks only the rest.

Nothing new is generated: questions are the tree's own cards, answers its own choices, and the
walk ends on a signed-off, cited card. A slot map (one YAML per tree, kept beside the expert
seeds, personal ones gitignored) says which question card asks which blank:

    tree: bites
    life_threat:                      # checked in order before anything else; first match opens
      - {if: {breathing: "no"}, go: wfa-cpr/cpr}
      - {if: {conscious: "no", breathing: "yes"}, go: wfa-cpr/breathing}   # recovery position, not CPR
      - {if: {conscious: "no"}, go: wfa-cpr/start}                         # asks about breathing first
      - {if: {bleeding_type: arterial}, go: wfa-bleeding/severe}
    slots:
      bleeding:
        severe: [gushing, spurting, pulsing, "won't stop", pouring]
        minor:  [oozing, "a little blood"]
      animal:
        snake:  [snake, rattlesnake, rattler]
        mammal: [dog, cat, raccoon, bat, squirrel, coyote, fox]
    questions:                        # card -> the blank it asks, and which answer each value takes
      start: {slot: animal, choices: {snake: snake, mammal: mammal}}

Trees may instead carry this inline (plb schema, 11-16): `slot:` on a question card and `value:`
on each of its choices; the slot map then only needs `slots` (the words) and `life_threat`.
Shared slot names: injury, body_part, animal, bleeding_type, severity, conscious, breathing, has_kit.

Rules: a blank is filled only by a word the person actually said (never guessed); a negated or
hedged word fills nothing ("not spurting", "can't tell if she's breathing"); the value whose phrases
cover the most heard words wins, ties go to `priority: {slot: [most severe first]}`; `implies:
{slot: {forearm: arm}}` lets a fine answer satisfy a coarser card; a `life_threat` rule (optional
`unless: {seizing: "yes"}`) opens its card first, and if that card is in this tree the walk goes on
from it; a card with no slot is passed only when exactly one way on reaches an answered blank
(the passed card is listed in `skipped` so the screen can offer it).

    python -m vr.intake bites --trees DIR [DIR] --slots FILE "a raccoon bit my leg"
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from .data import load_tree
from .lexicon import NEGATION, tokenize

NEG_WINDOW = 3


def _stem(w: str) -> str:
    """Light stemming so "bandages" matches "bandage" and "deeply" matches "deep"."""
    if len(w) > 5 and w.endswith("ly"):
        w = w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    return w


def _find(words: list[str], phrase: list[str]) -> int:
    n = len(phrase)
    for i in range(len(words) - n + 1):
        if words[i:i + n] == phrase:
            return i
    return -1


HEDGE = {"if", "whether", "unsure", "maybe", "barely", "might", "check"}


def fill(text: str, slots: dict, priority: dict | None = None) -> dict[str, str]:
    """Blank -> value, from the words actually heard. Never guesses:
    - a hit right after a negation ("not spurting") or a hedge ("can't tell if she's breathing") fills nothing;
    - the value whose phrases cover the most heard words wins ("red hot oozing pus" beats "cut");
    - a tie goes to the value listed first in priority[slot] (most severe / most specific first),
      else the one listed first in the slot map."""
    words = [_stem(w) for w in tokenize(text)]
    priority = priority or {}
    best: dict[str, tuple] = {}
    for slot, values in slots.items():
        order = list(priority.get(slot, [])) + [v for v in values if v not in priority.get(slot, [])]
        for value, phrases in values.items():
            covered = set()
            for ph in phrases:
                p = [_stem(w) for w in tokenize(ph)]
                i = _find(words, p)
                if i < 0:
                    continue
                before = words[max(0, i - NEG_WINDOW):i]
                if any(w in NEGATION for w in before) and not any(w in NEGATION for w in p):
                    continue
                if any(w in HEDGE for w in before) and not any(w in HEDGE for w in p):
                    continue
                covered.update(range(i, i + len(p)))
            if covered:
                key = (len(covered), -order.index(value))
                if slot not in best or key > best[slot][0]:
                    best[slot] = (key, value)
    return {s: v for s, (_, v) in best.items()}


def _questions(tree: dict, smap: dict) -> dict:
    qs = dict(smap.get("questions", {}))
    for key, n in tree["nodes"].items():            # inline form: `slot:` on the card, `value:` on choices
        if n.get("slot") and key not in qs:
            qs[key] = {"slot": n["slot"], "choices": {c["value"]: c["to"] for c in n.get("choices", [])
                                                      if c.get("value") and c.get("to")}}
    return qs


def _answer(q: dict, v: str | None, implies: dict) -> str | None:
    """The choice a filled value takes, climbing `implies` (forearm -> arm) when the card asks coarser."""
    seen = set()
    while v and v not in seen:
        if v in q.get("choices", {}):
            return q["choices"][v]
        seen.add(v)
        v = implies.get(q["slot"], {}).get(v)
    return None


def _leads(tree, qs, start, filled, implies, limit=12) -> bool:
    """Does some question below `start` already have its blank answered?"""
    todo, seen = [start], set()
    while todo and len(seen) < limit * 8:
        k = todo.pop()
        if k in seen or k not in tree["nodes"]:
            continue
        seen.add(k)
        if k in qs and _answer(qs[k], filled.get(qs[k]["slot"]), implies):
            return True
        todo += [c.get("to") for c in tree["nodes"][k].get("choices", []) if c.get("kind", "next") == "next"]
    return False


def walk(tree: dict, smap: dict, filled: dict[str, str]):
    """Follow the tree from its root, taking every answer the blanks already give.
    Returns (card to show, cards skipped, blank it is asking or None)."""
    nodes, qs, implies = tree["nodes"], _questions(tree, smap), smap.get("implies", {})
    node, skipped, seen = tree["root"], [], set()
    lt = smap.get("life_threat") or []
    if isinstance(lt, dict):                      # short form {slot: {value: card}}
        lt = [{"if": {s: v}, "go": card} for s, m in lt.items() for v, card in m.items()]
    for rule in lt:                               # life threats jump the queue; first matching rule wins
        if all(filled.get(s) == v for s, v in rule["if"].items()) and \
                not any(filled.get(s) == v for s, v in (rule.get("unless") or {}).items()):
            if rule["go"] not in nodes:
                return rule["go"], [], None        # another tree's card: open it
            node = rule["go"]                      # same tree: keep walking from there
            break
    while node not in seen:
        seen.add(node)
        q = qs.get(node)
        if q:
            v = filled.get(q["slot"])
            target = _answer(q, v, implies)
            if not target or target not in nodes:
                return node, skipped, q["slot"]    # blank still empty: ask it here
        else:                                      # plain card or menu: pass through only if exactly one
            nxt = [c.get("to") for c in nodes[node].get("choices", [])   # way on leads to an answered blank
                   if c.get("kind", "next") == "next" and c.get("to") in nodes]
            hits = [t for t in nxt if _leads(tree, qs, t, filled, implies)]
            if len(set(hits)) != 1:
                return node, skipped, None         # show this card
            target = hits[0]
        skipped.append(node)
        node = target
    return node, skipped, None


def _ancestors(tree: dict) -> dict[str, set[str]]:
    """Card -> every card on some path from the root to it (itself included)."""
    up: dict[str, set[str]] = {}
    for k, n in tree["nodes"].items():
        for c in n.get("choices", []):
            up.setdefault(c.get("to"), set()).add(k)
    out = {}
    for k in tree["nodes"]:
        seen, todo = {k}, [k]
        while todo:
            for p in up.get(todo.pop(), ()):
                if p not in seen:
                    seen.add(p); todo.append(p)
        out[k] = seen
    return out


def simulate(tree: dict, smap: dict, seeds: dict) -> str:
    """Score each seed line: 'right' = landed on its card (or a `same:` twin); 'on way' = stopped
    to ask a blank on a card that leads to it; 'stuck' = asking somewhere off its path;
    'WRONG' = opened a different answer card without asking (the case that must stay ~0)."""
    same = {}
    for grp in seeds.get("same", []):
        for k in grp:
            same[k] = set(grp)
    anc = _ancestors(tree)
    tally = {"right": 0, "on way": 0, "stuck": 0, "WRONG": 0}
    skipped_total, n, wrong = 0, 0, []
    for gold, d in (seeds.get("nodes") or {}).items():
        if gold not in tree["nodes"]:
            continue
        for line in (d.get("train") or []) + (d.get("test") or []):
            card, skipped, asking = walk(tree, smap, fill(line, smap["slots"], smap.get("priority")))
            n += 1
            skipped_total += len(skipped)
            ok = same.get(gold, {gold})
            if card in ok:
                tally["right"] += 1
            elif asking and any(card in anc.get(g, ()) for g in ok):
                tally["on way"] += 1
            elif asking or card == tree["root"]:
                tally["stuck"] += 1
            else:
                tally["WRONG"] += 1
                wrong.append(f"  {line!r}: wanted {gold}, opened {card}")
    pct = {k: f"{100 * v / max(n, 1):.0f}%" for k, v in tally.items()}
    return (f"{n} lines  " + "  ".join(f"{k} {v}" for k, v in pct.items())
            + f"  avg cards skipped {skipped_total / max(n, 1):.2f}\n" + "\n".join(wrong[:30]))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vr.intake")
    ap.add_argument("tree")
    ap.add_argument("sentences", nargs="*")
    ap.add_argument("--sim", type=Path, help="expert seed YAML: run every train+test line and score the walk")
    ap.add_argument("--trees", type=Path, nargs="+", required=True)
    ap.add_argument("--slots", type=Path, required=True)
    a = ap.parse_args(argv)
    tree = load_tree(a.trees, a.tree)
    smap = yaml.safe_load(a.slots.read_text())
    if a.sim:
        print(simulate(tree, smap, yaml.safe_load(a.sim.read_text())))
        return
    for s in a.sentences:
        f = fill(s, smap["slots"], smap.get("priority"))
        card, skipped, asking = walk(tree, smap, f)
        label = tree["nodes"].get(card, {}).get("label", card)
        print(f"{s!r}\n  filled: {f}\n  skipped {len(skipped)} card(s) {skipped} -> {card} ({label})"
              + (f", asks: {asking}" if asking else ""))


if __name__ == "__main__":
    main()
