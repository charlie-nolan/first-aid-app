"""Python -> C parity set for the tether / firmware port: the exact SHOW frames the PC bridge sends.

Each case runs one sentence through the engine the way firmware/tether/pc_bridge.py does (keyword
tree choice vr.live.keep_trees, then vr.form), then up to 3 answers, and records every SHOW frame:

    {"id", "text", "answers": [{"pick": {...}} | {"text": "..."}], "frames": [SHOW json, ...]}

Clearing a chip (the board selects it and presses TALK) is a PICK with "clear": true:
{"seq", "field": key, "value": null, "clear": true} -> vr.form.unfill(cands, key), then the usual
frame (a form with that blank open again, or a card).

Answers: variant "unfill" clears the first chip of the first form frame, then taps on as "pick"
(cases only when that frame has a chip). Variant "pick" taps the top field's first choice (a plain tile too: value null + node,
which the bridge passes whole as pick(field, None, cands, to=node)); variant "say" speaks the label
of the top field's first choice that sets a value, through vr.form.answer.

Node ids (vr.form.node_id): "tree/card"; a card in ANOTHER tree keeps its own id ("wfa-cpr/cpr",
never "fractures/wfa-cpr/cpr"). The board sends node ids back unchanged; vr.form.pick strips the
candidate's own "tree/" prefix.

Frame rules (a C port must produce the same bytes):
  * every frame: {"v": 1, "seq": n, "kind": K, "heard": <first hypothesis or answer text>[:100]}
    seq counts SHOW frames from 1 within a case. Only the keys below are added per kind.
  * kind "form": "fields", "filled", "top", "msg" ("" unless noted).
      fields = open_fields(cands) in order, each {"key", "q": question[:94],
               "choices": [...][:12], "after": [slot, value] | null}; a field with no usable
               choice is dropped; at most 6 fields (fields[:6], after dropping).
      choice with a value: {"label": (label or value)[:46], "value": str(value)[:46]}
      plain tile (no value, has a target): {"label": (label or target)[:46], "value": null,
               "node": node_id(field.trees[0], target)[:78]}. Plain tiles appear only on the field
               being asked now (look-ahead fields carry answers only).
      choices with neither value nor target are skipped.
      filled = vr.form.filled_blanks(cands)[:8], each {"key", "q": q[:94], "label": label[:46]}: the
               blanks already filled (the chips row), kept trees in order, each in fill order, a
               key once. [] when nothing is filled.
      top  = ranked_cards(cands, 3) as [{"title": "<Tree title> > <card label>", "node": node_id}]
  * kind "card": "card": {"title", "cites": [ref strings][:6], "node": node_id} (never text).
  * kind "skip": "cards": [{"title", "node"}][:6] from vr.form.skip.
  * kind "tabs" is never sent: the first tab's form is shown with msg "Several problems: showing the first".
    Tabs are sorted by urgency (vr.form.life_rank): severe bleeding, CPR, choking, anaphylaxis, other
    life cards, head/spine, burns, shock, the rest. A first tab on a life card sends its "card" frame.
  * kind "again": "msg": "Didn't catch that (reject N). Hold TALK and say it again." (speech only).
  * kind "start" (no other keys unless msg):
      after "new" / "home" / "back" / empty words: no msg;
      "multi": msg "Next sentence = several problems";
      no blanks heard in any tree: msg "No blanks heard in any tree. Hold TALK and say what happened.";
      a tap with no problem open: msg "Hold TALK and say what happened."

    python -m vr.parity --trees DIR [DIR] --slots SLOTS_DIR --seeds FILE [FILE ...] --out OUT.jsonl
    python -m vr.parity ... --tabs [--lines LOCAL.txt] --out TABS.jsonl   (multi-injury tab parity)

Seeds: YAML with topics/reject (topics.yaml) or nodes (an expert seed file); train+test lines are
used. Use public seed files only; the frames hold tree labels, so keep personal outputs local.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from . import form as F
from .live import _title, keep_trees, load_all, rescue


class Screen:
    """The bridge's screen logic, without serial I/O or speech."""

    def __init__(self, trees):
        self.trees, self.seq, self.heard, self.cands, self.multi, self.frames = trees, 0, "", None, False, []

    def show(self, kind, **kw):
        self.seq += 1
        d = {"v": 1, "seq": self.seq, "kind": kind, "heard": self.heard[:100]}
        d.update(kw)
        self.frames.append(d)
        return d

    def _node(self, name, card):
        return F.node_id(name, card, self.trees[name][0]) if name in self.trees else card

    def _ref(self, name, card):
        return {"title": _title(self.trees, name, card)[0], "node": self._node(name, card)}

    def form_screen(self, msg=""):
        fields = []
        for f in F.open_fields(self.cands):
            ch = []
            for c in f.choices:
                if c.get("value"):
                    ch.append({"label": str(c.get("label") or c["value"])[:46], "value": str(c["value"])[:46]})
                elif c.get("to"):
                    ch.append({"label": str(c.get("label") or c["to"])[:46], "value": None,
                               "node": self._node(f.trees[0], c["to"])[:78]})
            if ch:
                fields.append({"key": f.key, "q": f.question[:94], "choices": ch[:12],
                               "after": list(f.when) if f.when else None})
        filled = [{"key": f["key"], "q": f["q"][:94], "label": f["label"][:46]}
                  for f in F.filled_blanks(self.cands)][:8]
        top = [self._ref(n, c) for n, c in F.ranked_cards(self.cands, 3)]
        return self.show("form", fields=fields[:6], filled=filled, top=top, msg=msg)

    def card_screen(self, name, card):
        t, cites = _title(self.trees, name, card)
        self.cands = None
        return self.show("card", card={"title": t, "cites": [str(c.get("ref", c)) if isinstance(c, dict) else str(c)
                                                             for c in cites][:6], "node": self._node(name, card)})

    def result(self, kind, name, card):
        if kind == "open":
            return self.card_screen(name, card)
        if kind == "skip":
            return self.show("skip", cards=[self._ref(n, c) for n, c in card[:6]])
        if kind == "tabs":
            self.cands = F.split_tabs(self.cands)[0]
            lead = self.cands[0]
            if F._life_target(lead):         # a life tab is first and stands on its card
                return self.card_screen(lead.name, lead.card)
            return self.form_screen("Several problems: showing the first")
        return self.form_screen()

    def said(self, text: str):
        self.heard = text
        words = text.strip().lower()
        if words in ("new", "home", "back") or not words:
            self.cands = None
            return self.show("start")
        if words == "multi":
            self.multi = True
            return self.show("start", msg="Next sentence = several problems")
        if self.cands is None:
            self.cands = keep_trees([text], self.trees)
            if not self.cands:
                self.cands = None
                return self.show("start", msg="No blanks heard in any tree. Hold TALK and say what happened.")
            r = F.first_sentence([text], self.cands, multi=self.multi)
            self.multi = False
        else:
            r = rescue([text], self.trees, self.cands) or F.answer(text, self.cands)
        return self.result(*r)

    def picked(self, p: dict):
        if self.cands is None:
            return self.show("start", msg="Hold TALK and say what happened.")
        if p.get("clear"):
            return self.result(*F.unfill(self.cands, p["field"]))
        if p.get("value") is None and p.get("node"):
            return self.result(*F.pick(p["field"], None, self.cands, to=p["node"]))
        return self.result(*F.pick(p["field"], p["value"], self.cands))


def seed_lines(files: list[Path]) -> list[str]:
    out = []
    for f in files:
        d = yaml.safe_load(Path(f).read_text()) or {}
        for t in (d.get("topics") or {}).values():
            out += list(t.get("train") or []) + list(t.get("test") or [])
        rej = d.get("reject") or {}
        out += list(rej.get("train") or []) + list(rej.get("test") or [])
        for n in (d.get("nodes") or {}).values():
            out += list(n.get("train") or []) + list(n.get("test") or [])
    seen, uniq = set(), []
    for l in out:
        k = " ".join(str(l).lower().split())
        if k and k not in seen:
            seen.add(k); uniq.append(k)
    return uniq


def run_case(trees, text: str, mode: str, steps: int = 3):
    s, answers = Screen(trees), []
    fr = s.said(text)
    if mode == "unfill":                  # clear the first chip, then tap on as "pick" does
        if fr["kind"] != "form" or not fr["filled"]:
            return [], s.frames
        p = {"seq": fr["seq"], "field": fr["filled"][0]["key"], "value": None, "clear": True}
        answers.append({"pick": p})
        fr = s.picked(p)
        mode, steps = "pick", steps - 1
    for _ in range(steps):
        if fr["kind"] != "form" or not fr["fields"]:
            break
        fld = fr["fields"][0]
        if mode == "pick":
            ch = fld["choices"][0]
            p = {"seq": fr["seq"], "field": fld["key"], "value": ch["value"], "label": ch["label"]}
            if ch["value"] is None:
                p["node"] = ch["node"]
            answers.append({"pick": p})
            fr = s.picked(p)
        else:
            ch = next((c for c in fld["choices"] if c["value"]), None)
            if ch is None:
                break
            answers.append({"text": ch["label"]})
            fr = s.said(ch["label"])
    return answers, s.frames


def tabs_case(trees, text: str):
    """Multi-injury (TALK+WIN_R) tab parity: keep_trees([text]) -> first_sentence(multi=True) ->
    split_tabs; one entry per tab from its lead (tab[0]). None unless the result is "tabs"."""
    cands = keep_trees([text], trees)
    if not cands or F.first_sentence([text], cands, multi=True)[0] != "tabs":
        return None
    return [{"lead": t[0].name, "node": F.node_id(t[0].name, t[0].card, t[0].tree),
             "rank": F.life_rank(t[0]), "asking": t[0].asking} for t in F.split_tabs(cands)]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vr.parity")
    ap.add_argument("--trees", type=Path, nargs="+", required=True)
    ap.add_argument("--slots", type=Path, required=True)
    ap.add_argument("--seeds", type=Path, nargs="+", required=True, help="public seed YAMLs only")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--tabs", action="store_true",
                    help="multi-injury tab parity instead: {id, text, tabs: [{lead, node, rank, asking}]}")
    ap.add_argument("--lines", type=Path, nargs="*", default=[],
                    help="extra plain-text lines (one per line, ids l0000...), e.g. local scenes; keep outputs local")
    a = ap.parse_args(argv)
    trees = load_all(a.trees, a.slots)
    lines = seed_lines(a.seeds)
    n = 0
    if a.tabs:
        extra = [l.strip() for f in a.lines for l in Path(f).read_text().splitlines() if l.strip()]
        with open(a.out, "w") as out:
            for pre, group in (("p", lines), ("l", extra)):
                for i, t in enumerate(group):
                    tabs = tabs_case(trees, t)
                    if tabs:
                        out.write(json.dumps({"id": f"{pre}{i:04d}", "text": t, "tabs": tabs}) + "\n")
                        n += 1
        print(f"{len(lines) + len(extra)} lines -> {n} tabs cases -> {a.out}")
        return
    with open(a.out, "w") as out:
        for i, t in enumerate(lines):
            for mode in ("pick", "say", "unfill"):
                answers, frames = run_case(trees, t, mode)
                if mode != "pick" and not answers:
                    continue
                out.write(json.dumps({"id": f"p{i:04d}-{mode}", "text": t, "answers": answers, "frames": frames}) + "\n")
                n += 1
    print(f"{len(lines)} seed lines -> {n} cases -> {a.out}")


if __name__ == "__main__":
    main()
