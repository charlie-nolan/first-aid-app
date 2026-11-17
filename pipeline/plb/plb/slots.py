"""Voice intake slots: the map the router reads, and the checks behind it.

Question cards carry `slot:` and their answers `value:` (schema.py). This module lists them,
checks that the wiring is sound, and writes the slot map (no manual text, only ids and values):

- every answer value is unique on its card (two answers can't fill the same blank the same way)
- every answer and every life_threat rule points at a card that exists
- every life_threat condition names a slot and a value that some card actually uses

life_threat rules live in a small YAML file (personal: content/voice/life_threat.personal.yaml):

    life_threat:
      - {if: {breathing: "no"}, go: wfa-cpr/cpr, note: "..."}

They are checked in order and the first match wins.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .trees import Issue, load_trees


@dataclass
class SlotCard:
    tree: str
    node: str
    slot: str
    values: list[tuple[str, str]]        # (value, target card id)
    navigation: int                      # answers without a value


@dataclass
class SlotReport:
    cards: list[SlotCard] = field(default_factory=list)
    vocab: dict[str, set[str]] = field(default_factory=dict)
    rules: list[dict] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)


def _target(tree: str, to: str) -> str:
    return to if "/" in to else f"{tree}/{to}"


def collect(tree_dir, rules_file: Path | None = None) -> SlotReport:
    rep = SlotReport()
    trees = [t for _, t in load_trees(tree_dir)]
    nodes = {f"{t['id']}/{k}" for t in trees for k in (t.get("nodes") or {})}
    for t in trees:
        tid = t["id"]
        for k, n in (t.get("nodes") or {}).items():
            where = f"{tid}/{k}"
            slot = n.get("slot")
            choices = n.get("choices") or []
            for c in choices:
                if _target(tid, str(c.get("to"))) not in nodes:
                    rep.issues.append(Issue("error", where, f"answer {c.get('label')!r} goes to "
                                                            f"unknown card {c.get('to')!r}"))
            if not slot:
                continue
            vals = [(str(c["value"]), _target(tid, str(c["to"]))) for c in choices if c.get("value")]
            seen: dict[str, str] = {}
            for v, to in vals:
                if v in seen and seen[v] != to:
                    rep.issues.append(Issue("error", where, f"value {v!r} is on two answers "
                                                            f"({seen[v]} and {to})"))
                seen.setdefault(v, to)
            if not vals:
                rep.issues.append(Issue("warning", where, f"slot {slot!r} but no answer has a value"))
            rep.cards.append(SlotCard(tid, k, slot, vals, len(choices) - len(vals)))
            rep.vocab.setdefault(slot, set()).update(v for v, _ in vals)
    # every card must be reachable from its own tree's first card (links into other trees count)
    edges = {f"{t['id']}/{k}": [_target(t["id"], str(c.get("to"))) for c in (n.get("choices") or [])]
             for t in trees for k, n in (t.get("nodes") or {}).items()}
    for t in trees:
        start = f"{t['id']}/{t.get('root')}"
        seen, todo = {start}, [start]
        while todo:
            for nxt in edges.get(todo.pop(), []):
                if nxt.startswith(t["id"] + "/") and nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        for k in t.get("nodes") or {}:
            if f"{t['id']}/{k}" not in seen:
                rep.issues.append(Issue("error", f"{t['id']}/{k}", "card can't be reached from the "
                                                                   "tree's first card"))
    if rules_file and rules_file.exists():
        data = yaml.safe_load(rules_file.read_text()) or {}
        for i, r in enumerate(data.get("life_threat") or [], 1):
            where = f"{rules_file.name} rule {i}"
            rep.rules.append(r)
            go = r.get("go")
            if go not in nodes:
                rep.issues.append(Issue("error", where, f"go: {go!r} is not a card"))
            for s, v in (r.get("if") or {}).items():
                v = str(v)
                if s not in rep.vocab:
                    rep.issues.append(Issue("error", where, f"slot {s!r} is not on any card"))
                elif v not in rep.vocab[s]:
                    rep.issues.append(Issue("error", where, f"{s}={v!r} is not a value of that slot "
                                                            f"({', '.join(sorted(rep.vocab[s]))})"))
    return rep


def check_voice(rep: SlotReport, tree_dir, voice_dir: Path) -> int:
    """Cross-check the voice router's slot files (voice_dir/<tree>.yaml, shape
    `slots: {slot: {value: [phrases]}}` plus optional `life_threat`) against the trees:
    every tree value has phrases there, every life_threat `go` is a card, phrases are lowercase.
    Returns how many files were checked; problems go to rep.issues."""
    trees = {t["id"]: t for _, t in load_trees(tree_dir)}
    nodes = {f"{tid}/{k}" for tid, t in trees.items() for k in (t.get("nodes") or {})}
    by_tree: dict[str, list[SlotCard]] = {}
    for c in rep.cards:
        by_tree.setdefault(c.tree, []).append(c)
    n = 0
    for f in sorted(Path(voice_dir).glob("*.yaml")):
        data = yaml.safe_load(f.read_text()) or {}
        tid = data.get("tree") or f.stem
        if tid not in trees:
            continue
        n += 1
        where = f"voice {f.name}"
        words = data.get("slots") or {}
        for c in by_tree.get(tid, []):
            for v, _ in c.values:
                if v not in (words.get(c.slot) or {}):
                    rep.issues.append(Issue("error", where, f"{c.tree}/{c.node} value {c.slot}={v} "
                                                            "has no phrases in the voice slot file"))
        for slot, vals in words.items():
            for v, phrases in (vals or {}).items():
                for ph in phrases or []:
                    if str(ph) != str(ph).lower():
                        rep.issues.append(Issue("error", where, f"{slot}.{v}: phrase {ph!r} is not lowercase"))
        for i, r in enumerate(data.get("life_threat") or [], 1):
            go = str(r.get("go"))
            if _target(tid, go) not in nodes:
                rep.issues.append(Issue("error", where, f"life_threat rule {i}: go {go!r} is not a card"))
    return n


def write_map(rep: SlotReport, dest: Path, title: str = "Voice slot map") -> Path:
    out = [f"# {title}", "",
           "Generated by `plb slots` from the tree files (ids and values only, no manual text).",
           "`slot:` is on question cards, `value:` on the answers that fill it. Answers without a",
           "value are navigation. The packer ignores both keys.", "", "## Slot vocabulary", ""]
    for s in sorted(rep.vocab):
        out.append(f"- **{s}**: {', '.join(sorted(rep.vocab[s]))}")
    if rep.rules:
        out += ["", "## life_threat (ordered, first match wins)", "", "```yaml", "life_threat:"]
        for r in rep.rules:
            cond = ", ".join(f"{k}: {v!r}" if str(v) in ("yes", "no") else f"{k}: {v}"
                             for k, v in (r.get("if") or {}).items())
            note = f"   # {r['note']}" if r.get("note") else ""
            out.append(f"  - {{if: {{{cond}}}, go: {r.get('go')}}}{note}")
        out.append("```")
    out += ["", "## Cards (tree/node, slot: value -> target)", ""]
    for c in rep.cards:
        nav = f"  (navigation: {c.navigation})" if c.navigation else ""
        out.append(f"- `{c.tree}/{c.node}` **{c.slot}**: "
                   + ", ".join(f"{v} -> {to}" for v, to in c.values) + nav)
    errs = [i for i in rep.issues if i.level == "error"]
    out += ["", f"Checks: {len(rep.cards)} slot cards, {len(rep.vocab)} slots, {len(rep.rules)} life_threat "
                f"rules, {len(errs)} error(s), {len(rep.issues) - len(errs)} warning(s)."]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(out) + "\n")
    return dest
