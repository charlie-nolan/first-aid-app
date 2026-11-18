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
import re
from pathlib import Path

import yaml

from .data import load_tree
from .lexicon import NEGATION as _LEX_NEGATION, tokenize as _lex_tokenize

# Typed or recognized without the apostrophe ("isnt breathing"): same word, and the negation
# guard must still see it. ("ill", "its", "were" are real words and stay as they are.)
_CONTRACT = {"isnt": "isn't", "cant": "can't", "dont": "don't", "didnt": "didn't", "doesnt": "doesn't",
             "wont": "won't", "wasnt": "wasn't", "arent": "aren't", "havent": "haven't", "hasnt": "hasn't",
             "couldnt": "couldn't", "wouldnt": "wouldn't", "shouldnt": "shouldn't", "werent": "weren't",
             "aint": "ain't", "im": "i'm", "ive": "i've", "hes": "he's", "shes": "she's", "thats": "that's",
             "theyre": "they're", "youre": "you're", "theres": "there's"}
NEGATION = _LEX_NEGATION | {"hasn't", "couldn't", "wouldn't", "shouldn't", "weren't", "ain't", "nobody"}


def tokenize(text: str) -> list[str]:
    out = []
    for w in _lex_tokenize(text):
        w = _CONTRACT.get(w, w)
        if w == "to" and out and out[-1] == "going":
            out[-1] = "gonna"                   # "going to pass out" is a hedge, "going numb" is not
            continue
        out.append(w)
    return out

NEG_WINDOW = 3


def _stem(w: str, verbs: bool = True) -> str:
    """Light stemming so "bandages"/"bandage", "deeply"/"deep", "stopped"/"stop"/"stopping" match.
    Negation and hedge words are left alone ("nothing" stays "nothing"). verbs=False keeps -ing/-ed
    (used for one-word phrases: "squirting" must not hear "squirt bottle")."""
    if w in NEGATION or w in HEDGE or "'" in w:
        return w
    if len(w) > 5 and w.endswith("ly"):
        w = w[:-2]
    if not verbs:
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        return w
    if len(w) > 5 and w.endswith("ing"):
        w = w[:-3]
    elif len(w) > 4 and w.endswith("ed") and not w.endswith("eed"):
        w = w[:-2]
    elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "aeiouls":
        w = w[:-1]                          # stopp -> stop, cutt -> cut (keep "swell", "fall")
    return w


def _find(words: list[str], phrase: list[str], gap: int = 1) -> list[int] | None:
    """Positions of the phrase's words in `words`. Exact run first; else allow up to `gap` filler
    word between consecutive phrase words ("throat is tight" for "throat tight"), never a
    negation or hedge word ("throat not tight" does not match)."""
    n = len(phrase)
    for i in range(len(words) - n + 1):
        if words[i:i + n] == phrase:
            return list(range(i, i + n))
    if n < 2 or any(w in NEGATION or w in HEDGE for w in phrase):
        return None                      # "no breathing" must never match "no trouble breathing"
    for i, w in enumerate(words):
        if w != phrase[0]:
            continue
        pos, j = [i], i
        for p in phrase[1:]:
            for k in range(j + 1, min(j + 2 + gap, len(words))):
                if words[k] == p and all(x not in NEGATION and x not in HEDGE for x in words[j + 1:k]):
                    pos.append(k); j = k
                    break
            else:
                break
        if len(pos) == n:
            return pos
    return None


HEDGE = {"if", "whether", "unsure", "maybe", "barely", "might", "check",
         "will", "gonna", "could", "about", "sure", "tell"}   # "will pass out", "not sure he's breathing"


NOT_A_STATE = {"problem", "problems", "trouble", "troubles", "issue", "issues", "difficulty",
               "difficulties", "question", "questions", "advice", "tips", "rate"}

CONTRAST = {"but", "now", "though", "although", "except"}

# A negation or hedge does not reach past the end of its clause.
CLAUSE = CONTRAST | {"and", "so", "because", "cause", "then", "while", "plus", "also", "since"}
# A new subject starts a new clause for negation ("won't respond she's unconscious"), not for a
# hedge ("check if she's breathing").
SUBJECT = {"she's", "he's", "it's", "they're", "i'm", "we're", "you're", "she", "he", "they", "we"}
# Verbs that take what follows as their object, so the negation carries on ("not seeing any bleeding").
_SEEING = {"seeing", "finding", "having", "getting", "feeling", "noticing", "showing"}


def _ing(w: str) -> bool:
    return len(w) > 4 and w.endswith("ing")


def _scope(raw: list[str], i: int, neg: bool) -> list[str]:
    """The up-to-NEG_WINDOW words before raw[i] that a negation (neg=True) or hedge can reach from:
    stops at a clause word, (negation only) at a new subject, and (negation only) between two -ing
    words ("not breathing freezing outside": "not" is about breathing; "not bleeding heavily" stays negated)."""
    out = []
    for j in range(i - 1, max(-1, i - 1 - NEG_WINDOW), -1):
        w = raw[j]
        if w in CLAUSE:
            break
        if neg and w in SUBJECT:
            break
        if neg and j == i - 1 and _ing(w) and _ing(raw[i]) and w not in _SEEING:
            break                       # two participles in a row: two states ("not breathing freezing")
        out.append(w)
    return out


def fill(text: str, slots: dict, priority: dict | None = None, decisive: set | None = None,
         implies: dict | None = None) -> dict[str, str]:
    """Blank -> value, from the words actually heard. Never guesses:
    - a hit right after a negation ("not spurting") or a hedge ("can't tell if she's breathing") fills nothing;
    - "X but Y" / "X, now Y": when values of one blank sit on both sides of the contrast word,
      the later clause wins ("passed out but woke up");
    - the value whose phrases cover the most heard words wins ("red hot oozing pus" beats "cut");
    - a tie on coverage: if `decisive` is given, slots in it (the life-threat blanks) take the value
      listed first in priority[slot] (most severe first) and every other blank stays EMPTY, so the
      card asks; with decisive=None every tie takes the priority value (old behaviour)."""
    raw = tokenize(text)
    words = [_stem(w) for w in raw]
    plain = [_stem(w, verbs=False) for w in raw]
    priority = priority or {}
    cuts = [i for i, w in enumerate(words) if w in CONTRAST]
    out: dict[str, str] = {}
    for slot, values in slots.items():
        order = list(priority.get(slot, [])) + [v for v in values if v not in priority.get(slot, [])]
        cand = []                                  # (value, covered positions)
        for value, phrases in values.items():
            covered = set()
            for ph in phrases:
                pw = tokenize(ph)
                if len(pw) == 1:                   # one word: no verb folding ("squirting" != "squirt bottle")
                    p = [_stem(w, verbs=False) for w in pw]
                    hit = _find(plain, p)
                else:
                    p = [_stem(w) for w in pw]
                    hit = _find(words, p)
                if hit is None:
                    continue
                if hit[-1] + 1 < len(raw) and raw[hit[-1] + 1] in NOT_A_STATE:
                    continue                       # "no breathing problems": a topic, not a state
                if (len(pw) > 1 and pw[-1] == "out" and pw[-2] in ("bleed", "bleeding", "bleeds", "bled")
                        and hit[-1] + 1 < len(raw) and raw[hit[-1] + 1] == "of"):
                    continue                       # "bleeding out of his ear": where from, not how much
                i = hit[0]
                if any(w in NEGATION for w in _scope(raw, i, True)) and not any(w in NEGATION for w in p):
                    continue
                if any(w in HEDGE for w in _scope(raw, i, False)) and not any(w in HEDGE for w in p):
                    continue
                covered.update(hit)
            if covered:
                cand.append((value, covered))
        if not cand:
            continue
        for c in reversed(cuts):                   # later clause wins over an earlier one
            late = [x for x in cand if min(x[1]) > c]
            if late and len(late) < len(cand) and all(max(x[1]) < c for x in cand if x not in late):
                cand = late
                break
        top = max(len(cv) for _, cv in cand)
        tied = sorted((v for v, cv in cand if len(cv) == top), key=order.index)
        up = (implies or {}).get(slot, {})
        if len(tied) > 1 and not set(raw) & UNSURE_JOIN:   # "leg ... shin": lower_leg implies leg,
            coarse = set()                         # so the finer answer says both (board run s001);
                                                   # "wrist or elbow", "between shoulder and elbow": ask
            for v in tied:
                u, seen = up.get(v), {v}
                while u and u not in seen:
                    coarse.add(u)
                    seen.add(u)
                    u = up.get(u)
            tied = [v for v in tied if v not in coarse] or tied
        if len(tied) > 1 and decisive is not None and slot not in decisive:
            continue                               # two equally strong answers: ask, don't pick
        out[slot] = tied[0]
    return out


def decisive_slots(smap: dict) -> set:
    """Blanks used by life-threat rules: ties there go to the more severe value instead of asking."""
    lt = smap.get("life_threat") or []
    if isinstance(lt, dict):
        return set(lt)
    return {s for r in lt for s in list(r.get("if", {})) + list(r.get("unless") or {})}


def _fill(text: str, smap: dict) -> dict[str, str]:
    return fill(text, smap["slots"], smap.get("priority"), decisive_slots(smap), smap.get("implies"))


def fill_nbest(hyps: list[str], smap: dict) -> dict[str, str]:
    """Blanks from the recognizer's n-best (best first). The top hypothesis fills the blanks; a
    lower hypothesis may only ADD a life-threat answer the top one missed ("she's stop breathing"
    on top, "she stopped breathing" 3rd -> breathing=no). Lower guesses never replace or add
    anything else, so a misheard word can only make the device go to a safety card or ask."""
    if not hyps:
        return {}
    out = _fill(hyps[0], smap)
    lt = smap.get("life_threat") or []
    if isinstance(lt, dict):
        lt = [{"if": {s: v}} for s, m in lt.items() for v in m]
    danger = {(s, v) for r in lt for s, v in r.get("if", {}).items()}
    top = set(tokenize(hyps[0]))
    for h in hyps[1:]:
        new = set(tokenize(h)) - top
        if any(w2 in top for w in new for w2 in SOUNDALIKE.get(w, ())):
            continue                               # "stop breathing" below "stop bleeding": a misheard twin, not news
        for s, v in _fill(h, smap).items():
            if s not in out and (s, v) in danger:
                out[s] = v
    return out


def life_options(hyps: list[str], tree: dict, smap: dict) -> list[str]:
    """Cards to open from a recognizer n-best. Usually one. When the top guess triggers a
    life-threat jump through a word whose sound-alike is in a lower guess ("wind stop breathing"
    on top, "won't stop bleeding" below), BOTH outcomes come back so the screen shows the two
    life tiles (CPR | bleeding) instead of auto-opening either: one tap, never wrong unasked."""
    if not hyps:
        return []
    filled = fill_nbest(hyps, smap)
    first, _, _ = walk(tree, smap, filled)
    lt = smap.get("life_threat") or []
    pins = {c for m in lt.values() for c in m.values()} if isinstance(lt, dict) else {r["go"] for r in lt}
    pins.discard(tree["root"])                    # a tree's own start card is where you are, not a jump
    out = [first]
    top = tokenize(hyps[0])
    near = {w for h in hyps[1:3] for w in tokenize(h)}     # rescue only from the next two guesses
    below = {w for h in hyps[1:] for w in tokenize(h)}     # protection from any guess
    for i, w in enumerate(top):
        for alt in SOUNDALIKE.get(w, ()):
            heard = lambda pool: any(b == alt or _stem(b) == _stem(alt) for b in pool)
            if not heard(below) or any(x in NEGATION for x in top[max(0, i - NEG_WINDOW):i]):
                continue                           # "not breathing" vs "not bleeding": no rival reading
            swapped = " ".join(top[:i] + [alt] + top[i + 1:])
            card, _, _ = walk(tree, smap, _fill(swapped, smap))
            if first in pins:                      # protect: never auto-open a life card a sound-alike contradicts;
                card = (smap.get("life_words") or {}).get(_stem(alt), card)   # name the rival (bleed -> severe)
            elif not (card in pins and heard(near)):
                continue                           # rescue only when the swapped reading is itself a life threat
            if card not in out:
                out.append(card)
    return out


# Words the recognizer swaps for each other; a lower guess that only differs by one of these
# must not add a life threat the top guess explains another way.
_PAIRS = [("breathing", "bleeding"), ("breathe", "bleed"), ("breath", "blood")]   # both real first-aid words
SOUNDALIKE: dict[str, set] = {}
for _a, _b in _PAIRS:
    SOUNDALIKE.setdefault(_a, set()).add(_b)
    SOUNDALIKE.setdefault(_b, set()).add(_a)


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


MENU_SLOTS = ("missing_kit",)
UNSURE_JOIN = {"or", "between", "either"}


def _menu_blank(tree, smap, qs, node, slot) -> bool:
    """A kit branch the form never asks (board run 11-17: "Apply a tourniquet" showed as a field
    with [No tourniquet], "Forearm, elbow or wrist" with [Splinting rules, No sling, When to
    evacuate]): a menu_slots blank (default missing_kit) whose answers all lead to another tree's
    card (improvised_gear/...). The tile stays on the card, picked by tap or by saying it. In the
    improvised_gear tree itself the same blank is the real question and is asked."""
    if slot not in (smap.get("menu_slots") or MENU_SLOTS):
        return False
    to = [t["to"] for t in tiles(tree, smap, node) if t.get("value")]
    return bool(to) and all(t not in tree["nodes"] and "/" in t for t in to)


def walk(tree: dict, smap: dict, filled: dict[str, str], start: str | None = None):
    """Follow the tree from its root (or from `start`, after an answer), taking every answer the
    blanks already give. Returns (card to show, cards skipped, blank it is asking or None)."""
    nodes, qs, implies = tree["nodes"], _questions(tree, smap), smap.get("implies", {})
    node, skipped, seen = start or tree["root"], [], set()
    lt = [] if start else (smap.get("life_threat") or [])
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
            if target in nodes and not _leads(tree, qs, target, filled, implies):
                side = [c.get("to") for c in nodes[node].get("choices", [])   # "Bone through the skin"
                        if not c.get("value") and c.get("to") in qs        # beside the body parts, and
                        and qs[c["to"]]["slot"] != q["slot"]               # the first sentence said so:
                        and _answer(qs[c["to"]], filled.get(qs[c["to"]]["slot"]), implies)]
                if len(side) == 1:                 # the named finding beats a location that leads
                    target = side[0]               # nowhere further (board run s002)
            if target and target not in nodes and "/" in target:
                return target, skipped, None       # the answer leads to another tree's card: open it
            if not target or target not in nodes:
                # value not on this card ("spider" on a snake / sting / "spider, tick or animal" card),
                # or blank still empty but another blank answered below ("bone through the skin" tile
                # on the where card, open_fracture already said): follow the one plain answer that
                # leads to a card which takes it
                plain = [c.get("to") for c in nodes[node].get("choices", [])
                         if not c.get("value") and c.get("kind", "next") == "next" and c.get("to") in nodes]
                hits = {t for t in plain if _leads(tree, qs, t, filled, implies)}
                target = hits.pop() if len(hits) == 1 else None
            if not target or target not in nodes:
                if _menu_blank(tree, smap, qs, node, q["slot"]):
                    return node, skipped, None     # a kit branch or a menu, not a blank: show the card
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


NUMBERS = {"one": 0, "1": 0, "first": 0, "two": 1, "2": 1, "second": 1, "to": None, "too": None,
           "three": 2, "3": 2, "third": 2, "four": 3, "4": 3, "fourth": 3, "for": None}
COMMANDS = ("back", "again", "home")


def tiles(tree: dict, smap: dict, card: str) -> list[dict]:
    """The answers a question page shows, in screen order: {label, to, value}."""
    out = [dict(c) for c in tree["nodes"].get(card, {}).get("choices", []) if c.get("to")]
    q = _questions(tree, smap).get(card)
    if q:
        have = {c["to"] for c in out}
        out += [{"label": v, "to": t, "value": v} for v, t in q.get("choices", {}).items() if t not in have]
        for c in out:                              # inline cards carry `value:`; slot-map cards get it here
            c.setdefault("value", next((v for v, t in q.get("choices", {}).items() if t == c["to"]), None))
    for c in out:                                  # a choice with no label shows its value or target's label
        if not c.get("label"):
            c["label"] = c.get("value") or tree["nodes"].get(c["to"], {}).get("label") or c["to"]
    return out


def answer_turn(text: str, tree: dict, smap: dict, card: str, asking: str | None, filled: dict):
    """One spoken answer on a page (Charlie 11-17: each question is a small expert with a short
    answer list). Returns (new filled, tile it picked or None, command or None).
    Order: a command word; a tile number ("two", "number 3"); yes/no on a two-way yes/no page;
    else the answer's own words through the slot mapper. Any other blank heard in the same breath
    is filled too (never overwriting one already filled), so free speech can answer several."""
    words = tokenize(text)
    filled = dict(filled)
    if not words:                                  # nothing said: answers nothing (never "No")
        return filled, None, None
    if len(words) <= 2 and words and words[-1] in COMMANDS:
        return filled, None, words[-1]
    ts = tiles(tree, smap, card)
    nums = [NUMBERS[w] for w in words if NUMBERS.get(w) is not None]
    if len(set(nums)) == 1 and len(words) <= 3 and nums[0] < len(ts):
        pick = ts[nums[0]]
    else:
        pick = None
        heard = _fill(text, smap)
        for k, v in heard.items():
            filled.setdefault(k, v)
        if asking and asking in heard:
            target = _answer(_questions(tree, smap).get(card, {}), heard[asking], smap.get("implies", {}))
            pick = next((c for c in ts if c["to"] == target), None)
        elif len(words) <= 3 and not set(words) - {"yes", "yeah", "yep", "no", "nope", "nah"}:
            yn = "yes" if set(words) & {"yes", "yeah", "yep"} else "no"
            if not (set(words) & {"yes", "yeah", "yep"} and set(words) & {"no", "nope", "nah"}):
                pick = next((c for c in ts if (c.get("value") or "").strip().lower() == yn), None) or \
                    next((c for c in ts if tokenize(c.get("label", ""))[:1] == [yn]), None)   # "No" tile, value not_impaled
        if pick is None:                           # the tile's own words: "the leg one", "arm or leg"
            lab = [(c, set(tokenize(c.get("label", ""))) - _FILLER) for c in ts]
            off = {w for i, w in enumerate(words)        # negated or hedged words name no tile
                   if any(x in NEGATION for x in _scope(words, i, True))   # ("not on the face")
                   or any(x in HEDGE for x in _scope(words, i, False))}
            hits = [c for c, lw in lab if lw and lw <= set(words) and not lw & off]
            pick = hits[0] if len(hits) == 1 else None
    if pick and asking and pick.get("value"):
        filled[asking] = pick["value"]
    return filled, pick, None


_DIGIT = {"1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "10": "ten"}
_FILLER = {"a", "an", "the", "or", "and", "of", "on", "in", "is", "it", "to", "my", "your", "i", "yes", "no"}


def wordlists(tree: dict, smap: dict) -> dict:
    """Per question card: the words its answer may use (for the speech thread's per-question
    decoder subset): the asked slot's phrase words, its tile labels' words, numbers and commands."""
    base = ["one", "two", "three", "four", "yes", "no", "back", "again", "home"]
    out = {}
    for card in tree["nodes"]:
        ts = tiles(tree, smap, card)
        if not ts:
            continue
        q = _questions(tree, smap).get(card)
        w = set(base)
        for c in ts:                               # digits in labels are said as words; drop parenthetical source names
            lab = re.sub(r"\([^)]*\)", " ", c.get("label", ""))
            w |= {_DIGIT.get(t, t) for t in tokenize(lab)}
        if q:                                      # only the answers THIS card's tiles take (fine values climb `implies`)
            for v, phrases in smap.get("slots", {}).get(q["slot"], {}).items():
                if _answer(q, v, smap.get("implies", {})):
                    for ph in phrases:
                        w |= set(tokenize(ph))
        out[card] = {"slot": q["slot"] if q else None, "tiles": [c.get("label") or c.get("value") for c in ts],
                     "words": sorted(w)}
    return out


def _ancestors(tree: dict, qs: dict | None = None) -> dict[str, set[str]]:
    """Card -> every card on some path from the root to it (itself included)."""
    up: dict[str, set[str]] = {}
    for k, n in tree["nodes"].items():
        for c in n.get("choices", []):
            up.setdefault(c.get("to"), set()).add(k)
    for k, q in (qs or {}).items():
        for t in q.get("choices", {}).values():
            up.setdefault(t, set()).add(k)
    out = {}
    for k in tree["nodes"]:
        seen, todo = {k}, [k]
        while todo:
            for p in up.get(todo.pop(), ()):
                if p not in seen:
                    seen.add(p); todo.append(p)
        out[k] = seen
    return out


def turns_to_card(tree: dict, smap: dict, line: str, ok: set, anc: dict, most: int = 6,
                  say: str = "number") -> int | None:
    """Voice turns until the right card shows: turn 1 is the spoken sentence; every later turn is
    one answer on the page the device stopped on, run through answer_turn. say="number" answers
    with the right tile's number (or a button press: always understood); say="label" says the
    right tile's own words, which tests the per-question answer mapping. None = it opened a wrong
    card, asked off the path, or took the wrong tile (the person must back out)."""
    filled = dict(_fill(line, smap))
    card, _, asking = walk(tree, smap, filled)
    toward = set().union(*(anc.get(g, {g}) for g in ok))
    for turn in range(1, most + 1):
        if card in ok:
            return turn
        if card not in toward:
            return None
        ts = tiles(tree, smap, card)
        k = next((i for i, c in enumerate(ts) if c["to"] in toward), None)
        if k is None:
            return None
        text = f"number {k + 1}" if say == "number" or k > 3 else (ts[k].get("label") or ts[k].get("value") or "")
        if k > 3:                                  # past the 4 numbered tiles: a button press
            filled, pick = dict(filled), ts[k]
            if asking and pick.get("value"):
                filled[asking] = pick["value"]
        else:
            filled, pick, _ = answer_turn(text, tree, smap, card, asking, filled)
        if not pick or pick["to"] != ts[k]["to"]:
            return None
        if asking and pick.get("value"):
            card, _, asking = walk(tree, smap, filled, start=card)
        else:
            card, _, asking = walk(tree, smap, filled, start=pick["to"])
    return None


def simulate(tree: dict, smap: dict, seeds: dict) -> str:
    """Score each seed line: 'right' = landed on its card (or a `same:` twin); 'on way' = stopped
    to ask a blank on a card that leads to it; 'stuck' = asking somewhere off its path;
    'WRONG' = opened a different answer card without asking (the case that must stay ~0)."""
    same = {}
    for grp in seeds.get("same", []):
        for k in grp:
            same[k] = set(grp)
    anc = _ancestors(tree, _questions(tree, smap))
    tally = {"right": 0, "on way": 0, "stuck": 0, "WRONG": 0}
    from .form import first_rank, form_turns   # the fill-in-the-blanks screen (vr/form.py)
    ranks = []
    skipped_total, n, wrong, turns, said, allat = 0, 0, [], [], [], []
    lt = smap.get("life_threat") or []
    pins = {c for m in lt.values() for c in m.values()} if isinstance(lt, dict) else {r["go"] for r in lt}
    pinned = []                                   # (opened on turn 1?) for lines whose card is a life-threat pin
    for gold, d in (seeds.get("nodes") or {}).items():
        if gold not in tree["nodes"]:
            continue
        for line in (d.get("train") or []) + (d.get("test") or []):
            card, skipped, asking = walk(tree, smap, _fill(line, smap))
            n += 1
            skipped_total += len(skipped)
            ok = same.get(gold, {gold})
            turns.append(turns_to_card(tree, smap, line, ok, anc))
            said.append(turns_to_card(tree, smap, line, ok, anc, say="label"))
            allat.append(form_turns(tree.get("id", "tree"), tree, smap, line, ok, anc))
            ranks.append(first_rank(tree.get("id", "tree"), tree, smap, line, ok, anc))
            if ok & pins:
                pinned.append(turns[-1] == 1)
            if card in ok:
                tally["right"] += 1
            elif any(card in anc.get(g, ()) for g in ok):   # stopped (asking or on a plain card) on its way
                tally["on way"] += 1
            elif asking or card == tree["root"]:
                tally["stuck"] += 1
            else:
                tally["WRONG"] += 1
                wrong.append(f"  {line!r}: wanted {gold}, opened {card}")
    pct = {k: f"{100 * v / max(n, 1):.0f}%" for k, v in tally.items()}
    return (f"{n} lines  " + "  ".join(f"{k} {v}" for k, v in pct.items())
            + f"  avg cards skipped {skipped_total / max(n, 1):.2f}\n"
            + "".join(f"right card by voice turn ({how})  " + "  ".join(
                f"{k}: {100 * sum(1 for t in ts if t and t <= k) / max(n, 1):.0f}%" for k in (1, 2, 3, 4))
                + f"  never: {100 * sum(1 for t in ts if t is None) / max(n, 1):.0f}%\n"
                for how, ts in (("tile numbers", turns), ("saying the tile words", said),
                                ("fill-in-the-blanks screen, vr.form", allat)))
            + _rank_lines(ranks)
            + (f"life-threat cards opened on the first sentence: {sum(pinned)}/{len(pinned)}\n" if pinned else "")
            + "\n".join(wrong[:30]))


def _rank_lines(ranks) -> str:
    """Right card in the ranked list after the first sentence alone (top 1 / top 3), overall and
    by how much of its path the sentence filled."""
    if not ranks:
        return ""
    def row(rs):
        n = max(len(rs), 1)
        return (f"top1 {100 * sum(1 for r, _ in rs if r == 0) / n:.0f}%  "
                f"top3 {100 * sum(1 for r, _ in rs if r is not None and r < 3) / n:.0f}%  (n={len(rs)})")
    out = "right card ranked after the first sentence: " + row(ranks) + "\n"
    for lo, hi in ((0, .4), (.4, .6), (.6, .8), (.8, 1.01)):
        rs = [x for x in ranks if lo <= x[1] < hi]
        if rs:
            out += f"  blanks filled {int(lo * 100)}-{min(100, int(hi * 100))}%: " + row(rs) + "\n"
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vr.intake")
    ap.add_argument("tree")
    ap.add_argument("sentences", nargs="*")
    ap.add_argument("--sim", type=Path, help="expert seed YAML: run every train+test line and score the walk")
    ap.add_argument("--trees", type=Path, nargs="+", required=True)
    ap.add_argument("--slots", type=Path, required=True)
    ap.add_argument("--wordlists", action="store_true", help="print each question page's allowed answer words (YAML)")
    a = ap.parse_args(argv)
    tree = load_tree(a.trees, a.tree)
    smap = yaml.safe_load(a.slots.read_text())
    if a.wordlists:
        print(yaml.safe_dump({a.tree: wordlists(tree, smap)}, sort_keys=False, width=200))
        return
    if a.sim:
        print(simulate(tree, smap, yaml.safe_load(a.sim.read_text())))
        return
    for s in a.sentences:
        f = _fill(s, smap)
        card, skipped, asking = walk(tree, smap, f)
        label = tree["nodes"].get(card, {}).get("label", card)
        print(f"{s!r}\n  filled: {f}\n  skipped {len(skipped)} card(s) {skipped} -> {card} ({label})"
              + (f", asks: {asking}" if asking else ""))


if __name__ == "__main__":
    main()
