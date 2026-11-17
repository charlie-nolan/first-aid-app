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

Rules: a blank is filled only by a word the person actually said (never guessed), a negated
word fills nothing ("not spurting" leaves bleeding empty unless a 'not' phrase is listed), and
a `life_threat` value opens its card before any other question is asked.

    python -m vr.intake bites --trees DIR [DIR] --slots FILE "a raccoon bit my leg"
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from .data import load_tree
from .lexicon import NEGATION, tokenize

NEG_WINDOW = 3


def _find(words: list[str], phrase: list[str]) -> int:
    n = len(phrase)
    for i in range(len(words) - n + 1):
        if words[i:i + n] == phrase:
            return i
    return -1


def fill(text: str, slots: dict) -> dict[str, str]:
    """Blank -> value, from the words actually heard. Longest phrase wins; negated hits are dropped."""
    words = tokenize(text)
    out: dict[str, tuple[int, str]] = {}
    for slot, values in slots.items():
        for value, phrases in values.items():
            for ph in phrases:
                p = tokenize(ph)
                i = _find(words, p)
                if i < 0:
                    continue
                negated = any(w in NEGATION for w in words[max(0, i - NEG_WINDOW):i]) and not any(w in NEGATION for w in p)
                if negated:
                    continue
                if slot not in out or len(p) > out[slot][0]:
                    out[slot] = (len(p), value)
    return {s: v for s, (_, v) in out.items()}


def walk(tree: dict, smap: dict, filled: dict[str, str]):
    """Follow the tree from its root, taking every answer the blanks already give.
    Returns (card to show, cards skipped, blank it is asking or None)."""
    nodes, qs = tree["nodes"], dict(smap.get("questions", {}))
    for key, n in nodes.items():                 # inline form: `slot:` on the card, `value:` on choices
        if n.get("slot") and key not in qs:
            qs[key] = {"slot": n["slot"], "choices": {c["value"]: c["to"] for c in n.get("choices", [])
                                                      if c.get("value") and c.get("to")}}
    node, skipped, seen = tree["root"], [], set()
    lt = smap.get("life_threat") or []
    if isinstance(lt, dict):                      # short form {slot: {value: card}}
        lt = [{"if": {s: v}, "go": card} for s, m in lt.items() for v, card in m.items()]
    for rule in lt:                               # life threats jump the queue; first matching rule wins
        if all(filled.get(s) == v for s, v in rule["if"].items()):
            return rule["go"], [], None
    while node not in seen:
        seen.add(node)
        q = qs.get(node)
        if not q:
            return node, skipped, None             # not a mapped question: show this card
        v = filled.get(q["slot"])
        target = q.get("choices", {}).get(v) if v else None
        if not target or target not in nodes:
            return node, skipped, q["slot"]        # blank still empty: ask it here
        skipped.append(node)
        node = target
    return node, skipped, None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vr.intake")
    ap.add_argument("tree")
    ap.add_argument("sentences", nargs="+")
    ap.add_argument("--trees", type=Path, nargs="+", required=True)
    ap.add_argument("--slots", type=Path, required=True)
    a = ap.parse_args(argv)
    tree = load_tree(a.trees, a.tree)
    smap = yaml.safe_load(a.slots.read_text())
    for s in a.sentences:
        f = fill(s, smap["slots"])
        card, skipped, asking = walk(tree, smap, f)
        label = tree["nodes"][card].get("label", card)
        print(f"{s!r}\n  filled: {f}\n  skipped {len(skipped)} card(s) {skipped} -> {card} ({label})"
              + (f", asks: {asking}" if asking else ""))


if __name__ == "__main__":
    main()
