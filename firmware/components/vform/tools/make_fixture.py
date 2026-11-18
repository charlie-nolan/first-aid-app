"""Made-up fixture trees (no manual text) -> test/fixture.fpk + test/fixture_parity.jsonl, both safe to
commit. The parity frames come from the Python reference itself (vr.parity.run_case), so the C host
test can be run by anyone without the personal trees:

    python make_fixture.py --vr <pipeline/voice_router at 5fa165f or later>
    cc -O2 -ffp-contract=off -o /tmp/vform_test ../host_test.c ../vform.c ../vform_show.c -lm
    python parity_check.py --bin /tmp/vform_test --pack ../test/fixture.fpk --parity ../test/fixture_parity.jsonl --fuzz
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Small trees in the shapes the real ones use: slot-map questions, inline slot/value cards, plain
# menu tiles, a life rule into another tree, a sound-alike pair, implies, priority and negation.
BITES = {"root": "start", "title": "Bites", "nodes": {
    "start": {"label": "What bit you?"},
    "snake": {"label": "Snake bite", "cite": ["FIX/1.1"]},
    "mammal": {"label": "Where is the bite?", "choices": [
        {"label": "When to evacuate", "to": "evac"}, {"label": "Start CPR", "to": "cpr/cpr"}]},
    "limb": {"label": "Bite on an arm or leg", "cite": ["FIX/1.2", {"ref": "FIX/1.3"}]},
    "face": {"label": "Bite on the face"},
    "evac": {"label": "When to evacuate"},
    "sting": {"label": "Sting: is the stinger still in?", "slot": "stinger", "choices": [
        {"label": "Yes, I can see it", "to": "scrape", "value": "yes"},
        {"label": "No", "to": "wash", "value": "no"}]},
    "scrape": {"label": "Scrape the stinger out"}, "wash": {"label": "Wash and cool it"}}}
BITES_SMAP = {
    "life_threat": [{"if": {"breathing": "no"}, "go": "cpr/cpr"},
                    {"if": {"bleeding": "severe"}, "go": "bleeding/severe"}],
    "priority": {"animal": ["snake", "sting", "mammal"], "bleeding": ["severe", "minor"]},
    "implies": {"where": {"forearm": "limb", "thigh": "limb"}},
    "slots": {"animal": {"snake": ["snake", "rattler", "rattlesnake"], "mammal": ["dog", "raccoon", "bat", "cat"],
                         "sting": ["bee", "wasp", "stung", "sting"]},
              "where": {"limb": ["leg", "arm"], "forearm": ["forearm", "wrist"], "thigh": ["thigh"],
                        "face": ["face", "cheek", "nose"]},
              "bleeding": {"severe": ["gushing", "spurting", "won't stop", "pouring"], "minor": ["oozing", "a little blood"]},
              "breathing": {"no": ["not breathing", "stopped breathing"], "yes": ["breathing fine"]},
              "stinger": {"yes": ["stinger still in", "see the stinger"], "no": ["no stinger"]}},
    "questions": {"start": {"slot": "animal", "choices": {"snake": "snake", "mammal": "mammal", "sting": "sting"}},
                  "mammal": {"slot": "where", "choices": {"limb": "limb", "face": "face"}}}}
BURNS = {"root": "start", "title": "Burns", "nodes": {
    "start": {"label": "How big?", "slot": "size", "choices": [
        {"label": "Small, smaller than a palm", "to": "small", "value": "small"},
        {"label": "Big", "to": "big", "value": "big"}, {"label": "When to evacuate", "to": "evac"}]},
    "small": {"label": "Small burn"}, "big": {"label": "Big burn: where?", "slot": "where", "choices": [
        {"label": "Face or hands", "to": "face", "value": "face"}, {"label": "Arm or leg", "to": "limb", "value": "limb"}]},
    "face": {"label": "Burn on face or hands"}, "limb": {"label": "Burn on a limb"}, "evac": {"label": "When to evacuate"}}}
BURNS_SMAP = {"slots": {"size": {"small": ["small", "tiny"], "big": ["big", "large", "huge"]},
                        "where": {"limb": ["leg", "arm"], "face": ["face", "hand", "hands"]}}}
CPR = {"root": "start", "title": "CPR", "nodes": {
    "start": {"label": "Is she breathing?", "slot": "breathing", "choices": [
        {"label": "No", "to": "cpr", "value": "no"}, {"label": "Yes", "to": "breathing", "value": "yes"}]},
    "cpr": {"label": "Start CPR", "cite": ["FIX/9.1"], "slot": "cold", "choices": [
        {"label": "Very cold or from water", "to": "cold", "value": "yes"}]},
    "cold": {"label": "CPR when very cold"}, "breathing": {"label": "Recovery position"}}}
CPR_SMAP = {"life_threat": [{"if": {"breathing": "no"}, "go": "cpr"}],
            "slots": {"breathing": {"no": ["not breathing", "no pulse"], "yes": ["breathing fine", "is breathing"]},
                      "cold": {"yes": ["freezing", "from the water", "icy"]}}}
BLEEDING = {"root": "start", "title": "Bleeding", "nodes": {
    "start": {"label": "How bad is the bleeding?", "slot": "bleeding", "choices": [
        {"label": "Spurting or won't stop", "to": "severe", "value": "severe"},
        {"label": "Oozing", "to": "minor", "value": "minor"}]},
    "severe": {"label": "Stop the bleeding", "cite": ["FIX/5.1"]}, "minor": {"label": "Clean and cover"}}}
BLEEDING_SMAP = {"life_threat": {"bleeding": {"severe": "severe"}},
                 "slots": {"bleeding": {"severe": ["gushing", "spurting", "won't stop"], "minor": ["oozing", "scrape"]}}}

TREES = {"bites": (BITES, BITES_SMAP), "burns": (BURNS, BURNS_SMAP), "cpr": (CPR, CPR_SMAP),
         "bleeding": (BLEEDING, BLEEDING_SMAP)}

SENTENCES = [
    "a raccoon bit my thigh", "something bit me", "a dog bit his cheek", "the dog bite is gushing",
    "she's not breathing", "she's not breathing she fell in icy water", "he is breathing fine",
    "not spurting just oozing", "can't tell if she's breathing", "a rattler got him on the leg",
    "a bee stung me", "i got stung and i can see the stinger", "burned my hand on the stove, it's big",
    "small burn on my arm", "my arm hurts", "he stopped breathing", "a little blood from a scrape",
    "it won't stop bleeding", "cat bit my wrist", "burns, a big one", "huge burn", "bleeding but now oozing",
    "isnt breathing", "dog bite no time", "something bit me skip", "number two", "multi", "new",
    "a bat bit her face and she's not breathing", "wasp", "nothing bit me",
]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--vr", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=HERE.parent / "test")
    a = ap.parse_args(argv)
    sys.path.insert(0, str(a.vr.expanduser()))
    sys.path.insert(0, str(HERE))
    from formpack import build
    from vr.parity import run_case
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "fixture.fpk").write_bytes(build(TREES))
    n = 0
    with open(a.out / "fixture_parity.jsonl", "w") as f:
        for i, t in enumerate(SENTENCES):
            for mode in ("pick", "say"):
                answers, frames = run_case(TREES, t, mode)
                if mode == "say" and not answers:
                    continue
                f.write(json.dumps({"id": f"f{i:03d}-{mode}", "text": t, "answers": answers, "frames": frames}) + "\n")
                n += 1
    print(f"{a.out}/fixture.fpk, {n} cases")


if __name__ == "__main__":
    main()
