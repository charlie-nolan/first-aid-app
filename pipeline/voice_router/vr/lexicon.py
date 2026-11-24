"""Lexicon: word <-> id, shared with the speech recognizer thread.

Contract (agreed 2026-11-16 with "Build custom first-aid speech recognizer"):
  ids 0..4095 (cap raised from 1024 on 2026-11-16, to 4096 on 2026-11-17 for per-sector vocab), 0 = UNK, 1-4 one..four, 5 yes, 6 no, 7 back, 8 again, 9 home.
  1-byte class: 0 other, 1 body_part, 2 mechanism, 3 symptom, 4 negation, 5 quantity, 6 animal (11-16).
  Ids are append-only. The real table is pipeline/plb/content/lexicon.yaml (written by the
  recognizer thread); until it exists we harvest one from our own data with the same rules.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

CAP = 4096
RESERVED = ["<unk>", "one", "two", "three", "four", "yes", "no", "back", "again", "home"]
NEGATION = {"not", "no", "can't", "cannot", "don't", "isn't", "won't", "without", "never",
            "didn't", "doesn't", "wasn't", "aren't", "haven't", "nothing", "none"}
CLS_OTHER, CLS_BODY, CLS_MECH, CLS_SYMPTOM, CLS_NEG, CLS_QTY, CLS_ANIMAL = range(7)
SLOT_CLASSES = (CLS_BODY, CLS_MECH, CLS_ANIMAL)   # "types": the model also sees "bit <body part>"

# Default word types for a harvested lexicon (lexicon.yaml's own classes win when it exists).
BODY = set("""head face eye eyes ear ears nose mouth lip lips tongue tooth teeth jaw neck throat
shoulder shoulders arm arms elbow forearm wrist hand hands finger fingers thumb pinky chest rib ribs
back spine belly stomach abdomen gut guts hip hips pelvis groin butt leg legs thigh knee kneecap shin
calf ankle foot feet heel toe toes skin scalp collarbone armpit forehead cheek cheeks""".split())
MECH = set("""bit bite bitten bites stung sting stings cut cuts sliced slice gash gashed burned burn
burnt scalded fell fall fallen broke broken twisted rolled sprained dislocated hit struck stabbed
impaled crushed scraped punctured snapped popped slipped tripped crashed shot chomped nipped
scratched pinched""".split())
ANIMAL = set("""dog dogs cat cats raccoon bear snake rattlesnake rattler spider bee bees wasp
hornet tick ticks bat squirrel coyote cougar lion fox horse rat mouse deer moose elk centipede
scorpion ant ants mosquito jellyfish shark fish wolf skunk chipmunk marmot""".split())
DEFAULT_CLASSES = {**{w: CLS_BODY for w in BODY}, **{w: CLS_MECH for w in MECH}, **{w: CLS_ANIMAL for w in ANIMAL}}

_tok = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def tokenize(text: str) -> list[str]:
    t = text.lower().replace("’", "'")
    return _tok.findall(t)


class Lexicon:
    def __init__(self, words: list[str], classes: dict[str, int] | None = None):
        if words[: len(RESERVED)] != RESERVED:
            raise ValueError("lexicon must start with the reserved ids")
        if len(words) > CAP:
            raise ValueError(f"lexicon has {len(words)} words, cap is {CAP}")
        self.words = words
        self.ids = {w: i for i, w in enumerate(words)}
        self.cls = [0] * len(words)
        self.phones: list[list[str]] = [[] for _ in words]   # ARPAbet, when lexicon.yaml has it
        for w, c in (classes or {}).items():
            if w in self.ids:
                self.cls[self.ids[w]] = c
        for w in NEGATION:
            if w in self.ids:
                self.cls[self.ids[w]] = CLS_NEG

    def __len__(self):
        return len(self.words)

    def encode(self, text: str) -> list[int]:
        return [self.ids.get(w, 0) for w in tokenize(text)]

    def slot(self, i: int) -> int:
        """Word type if it is a slot type (body part, mechanism, animal), else 0."""
        c = self.cls[i] if 0 < i < len(self.cls) else 0
        return c if c in SLOT_CLASSES else 0

    def is_neg(self, i: int) -> bool:
        return self.cls[i] == CLS_NEG

    @classmethod
    def load_yaml(cls, path: Path) -> "Lexicon":
        d = yaml.safe_load(Path(path).read_text())
        rows = sorted(d["words"], key=lambda r: r["id"])
        words = [r["word"] for r in rows]
        lex = cls(words, {r["word"]: int(r.get("class", 0)) for r in rows})
        for r in rows:
            ph = r.get("phones") or []
            if ph:
                lex.phones[r["id"]] = ph[0].split()
        return lex

    @classmethod
    def harvest(cls, texts: list[str], extra_counts: dict[str, int] | None = None) -> "Lexicon":
        """Most frequent words first, after the reserved ids, up to the cap."""
        from collections import Counter
        c = Counter()
        for t in texts:
            c.update(tokenize(t))
        for w in RESERVED:
            c.pop(w, None)
        words = RESERVED + [w for w, _ in c.most_common(CAP - len(RESERVED))]
        return cls(words, DEFAULT_CLASSES)
