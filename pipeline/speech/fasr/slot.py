"""Mad-Libs slot decoding: listen for only one blank's words and snap to one of its values.

A slot spec is {value: [phrase, ...]}: a closed menu where each value has its own words and
slang ("snake": ["snake", "rattler", "rattlesnake", "copperhead"]). The clip is decoded with only
those words (anything else falls into the <unk> garbage path), then the n-best posterior mass is
summed per value: a hypothesis votes for every value whose phrase appears in it.

    decide(logp, spec) -> {"value", "conf", "runner_up", "runner_conf", "none", "action"}
    action: "fill" (sure), "choose" (show value + runner-up), "ask" (nothing close: leave the blank open)

    python -m fasr slot --run firstaid2 --spec spec.yaml --set clips.jsonl [--slot NAME]
spec.yaml: {slot_name: {value: [phrases]}}. With --set, each clip's gold value is found by phrase
matching on its text (clips with no value test the "ask" side). Prints fill accuracy, how often a
wrong value is filled, and how often an off-slot clip is filled at all.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

import yaml

from . import lexicon as lx
from .decode import Params, Trie, nbest

# Common little words that sound like menu words ("I" ~ eye, "or" ~ ear, "to" ~ toe, "are" ~ arm).
# They join every slot's decoding as distractors: hearing them votes for "none", so a free sentence
# doesn't fill a blank from a pronoun.
DISTRACTORS = """i i'm i've me my mine you your he his him she her hers it its we our us they their them a an the
and or but so if of to too two in on at by for from with into onto off up down out over under about after before
is are was were be been am do does did done have has had can can't could would should will won't not no yes
what where when who why how which this that there here these those all some any just very really now then
get got go going went come came see saw know think tell say said look looks like want need help
one three four five back again home okay ok oh um uh yeah well""".split()

SLOT_PARAMS = Params(beam=24, garbage_enter=-2.0, garbage_phone=-1.0)   # small menus: let other speech fall to <unk>


def build(spec: dict, rows: list[dict], distractors: bool = False):
    """spec {value: [phrases]} -> (trie, {value: [id tuples]}, unknown words)."""
    ids = {r["word"]: r["id"] for r in rows}
    phrases, missing, need = {}, set(), set()
    for val, ps in spec.items():
        for p in [val] + list(ps or []):
            toks = lx.tokenize(str(p))
            if not toks:
                continue
            if any(t not in ids for t in toks):
                missing.update(t for t in toks if t not in ids)
                continue
            phrases.setdefault(val, []).append(tuple(ids[t] for t in toks))
            need.update(toks)
    if distractors:
        need |= set(DISTRACTORS) - {t for ps in spec.values() for p in (ps or []) for t in lx.tokenize(str(p))}
    sub = [r for r in rows if r["word"] in need or r["id"] == 0]
    return Trie.from_lexicon(sub), phrases, missing


def _has(seq: list[int], ph: tuple) -> bool:
    n = len(ph)
    return any(tuple(seq[i:i + n]) == ph for i in range(len(seq) - n + 1))


def decide(logp, trie: Trie, phrases: dict, p: Params = SLOT_PARAMS,
           fill_at: float = 0.6, margin: float = 0.2, choose_at: float = 0.6, max_none: float = 1.0) -> dict:
    """max_none: a fill needs the "said none of these" mass below this (use ~0.3 when the blank is read
    off a free sentence: an unmentioned body part must not be guessed)."""
    hyp = nbest(logp, trie, p)
    mass = defaultdict(float)
    none = 0.0
    for h in hyp["h"]:
        hit = [v for v, phs in phrases.items() if any(_has(h["ids"], ph) for ph in phs)]
        if not hit:
            none += h["p"]
        for v in hit:
            mass[v] += h["p"]
    ranked = sorted(mass.items(), key=lambda kv: -kv[1]) + [(None, 0.0), (None, 0.0)]
    (v1, c1), (v2, c2) = ranked[0], ranked[1]
    if v1 is not None and c1 >= fill_at and c1 - c2 >= margin and none < max_none:
        action = "fill"
    elif v1 is not None and v2 is not None and c1 + c2 >= choose_at:
        action = "choose"
    else:
        action = "ask"
    return {"value": v1, "conf": round(c1, 3), "runner_up": v2, "runner_conf": round(c2, 3),
            "none": round(none, 3), "action": action}


def gold_value(text: str, phrases_txt: dict) -> str | None:
    words = lx.tokenize(text)
    best = None
    for v, ps in phrases_txt.items():
        for p in [v] + list(ps or []):
            t = lx.tokenize(str(p))
            if t and any(words[i:i + len(t)] == t for i in range(len(words) - len(t) + 1)):
                if best is None or len(t) > best[1]:
                    best = (v, len(t))
    return best[0] if best else None


def main(argv=None):
    import torch
    from .data import load_wav
    from .evaluate import load_model
    from .features import logmel
    ap = argparse.ArgumentParser(prog="fasr slot")
    ap.add_argument("--run", default="firstaid")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--set", required=True, action="append")
    ap.add_argument("--slot", action="append", help="only these slots (default all in the spec)")
    ap.add_argument("--max-none", type=float, default=1.0, help="0.3 for blanks read off a free sentence")
    ap.add_argument("--sentence", action="store_true",
                    help="blank read off a free first sentence: common little words compete (\"I\" is not \"eye\"); "
                         "default is answer mode (page 2: the person answers the blank itself)")
    a = ap.parse_args(argv)
    rows = yaml.safe_load(lx.LEXICON.read_text())["words"]
    spec = yaml.safe_load(Path(a.spec).read_text())
    model = load_model(a.run)
    clips = []
    for sp in a.set:
        sp = Path(sp)
        for l in sp.read_text().splitlines():
            if l.strip():
                r = json.loads(l)
                w = Path(r["wav"]) if Path(r["wav"]).is_absolute() else sp.parent / r["wav"]
                clips.append((str(w), r.get("text", "")))
    lps = []
    for w, _ in clips:
        with torch.no_grad():
            lps.append(model(torch.from_numpy(logmel(load_wav(w)))[None])[0].numpy())
    report = {}
    for name, menu in spec.items():
        if a.slot and name not in a.slot:
            continue
        trie, phrases, missing = build(menu, rows, distractors=a.sentence)
        g = defaultdict(int)
        for lp, (_, text) in zip(lps, clips):
            gold = gold_value(text, menu)
            d = decide(lp, trie, phrases, max_none=a.max_none)
            if gold:
                g["gold"] += 1
                g["filled_right"] += d["action"] == "fill" and d["value"] == gold
                g["filled_wrong"] += d["action"] == "fill" and d["value"] != gold
                g["choose_has_it"] += d["action"] == "choose" and gold in (d["value"], d["runner_up"])
                g["asked"] += d["action"] == "ask"
            else:
                g["off"] += 1
                g["off_filled"] += d["action"] == "fill"
        report[name] = {
            "values": len(menu), "clips_with_value": g["gold"],
            "filled_right": round(g["filled_right"] / max(1, g["gold"]), 3),
            "right_or_in_choice": round((g["filled_right"] + g["choose_has_it"]) / max(1, g["gold"]), 3),
            "filled_wrong": round(g["filled_wrong"] / max(1, g["gold"]), 3),
            "asked_again": round(g["asked"] / max(1, g["gold"]), 3),
            "off_slot_clips": g["off"], "off_slot_filled": round(g["off_filled"] / max(1, g["off"]), 3),
            "words_not_in_lexicon": sorted(missing)[:20],
        }
        print(name, json.dumps(report[name]), flush=True)
    return report


if __name__ == "__main__":
    main()
