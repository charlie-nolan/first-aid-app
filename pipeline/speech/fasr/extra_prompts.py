"""Recorder prompts for round 3: PAIRS (sentences around words the model confuses) and ANSWERS
(a device question plus a short answer, the way the intake pages will be used).

    python -m fasr devrec extra      # appends P| and A| lines to firmware/recorder/main/prompts.txt
                                     # (T| and E| lines are kept as they are, so saved positions stay valid)

Confusions come from the router's end-to-end check on Charlie's voice (2026-11-17). Each pair is
recorded in BOTH directions and in several carrier sentences, so the model hears the contrast in
the places it actually goes wrong. Every word used must be in lexicon.yaml (checked).
"""
from __future__ import annotations

import random

from . import lexicon as lx

# (word heard right, what it became) -> sentences using each side
PAIRS = {
    ("bleeding", "breathing"): [
        "he's bleeding a lot", "he's breathing a lot", "my leg won't stop bleeding", "she won't stop breathing hard",
        "is he still breathing", "is he still bleeding", "bleeding through the bandage", "breathing really fast",
        "she stopped bleeding", "she stopped breathing", "bleeding from the head", "breathing but not awake",
        "not breathing and bleeding", "bleeding and not breathing", "he's breathing fine but bleeding bad",
        "he's bleeding fine but not breathing"],
    ("stop", "drop"): [
        "it won't stop", "i can't stop the bleeding", "pressure to stop it", "stop the bleeding now",
        "drop the pack", "a big drop off the cliff", "he took a drop", "every drop of blood"],
    ("stopped", "dropped"): [
        "he stopped breathing", "she just stopped", "he dropped to the ground", "she dropped the stove",
        "it stopped and started again", "he dropped and stopped moving"],
    ("not", "numb"): [
        "he's not breathing", "she's not awake", "not moving at all", "my hand is numb", "my feet are numb",
        "numb and not moving", "not numb just sore", "he is not breathing and blue"],
    ("lips", "legs"): [
        "her lips are blue", "my lips are swelling", "both legs are numb", "his legs hurt",
        "lips and fingers turning blue", "legs won't move"],
    ("hit by", "hip bite"): [
        "hit by a rock", "hit by a falling tree", "my hip hurts", "a bite on my hip", "he got hit by lightning",
        "bit on the hip by a dog"],
    ("impaled", "field"): [
        "something impaled in his leg", "an impaled stick", "impaled object in the chest", "out in the field"],
    ("camp stove", "can't still"): [
        "burned by the camp stove", "the camp stove exploded", "can't keep still", "he can't sit still"],
    ("spurting", "squirting"): [
        "blood spurting out", "it's spurting with every heartbeat", "squirting blood", "spurting from his arm"],
    ("choking", "joking"): [
        "she's choking", "he's choking on food", "can't breathe choking", "i'm not joking he's choking"],
    ("chest", "test"): [
        "pain in my chest", "crushing chest pain", "chest feels tight", "hit in the chest"],
    ("asthma", "as my"): [   # board log 2026-11-22: "asthma" came out as "as my" twice
        "she's having an asthma attack", "asthma attack and can't breathe", "he has asthma", "my asthma is bad",
        "asthma and no inhaler", "as my friend said he has asthma", "bad asthma attack", "it's his asthma"],
    ("fell", "fall"): [
        "i fell", "he fell off a rock", "she fell and hit her head", "a bad fall", "he took a fall",
        "fell down the hill", "fell on the trail", "fall from a cliff"],
}

# Everyday first-aid words, said in a few plain sentences each. append() puts the ones Charlie has
# recorded fewer than COVER_MIN times FIRST in the PAIRS list, so the gaps get filled before anything else.
COVER_MIN = 3
COVER = {
    "inhaler": ["where is his inhaler", "she needs her inhaler", "no inhaler with us"],
    "fainted": ["she fainted", "he fainted and fell", "i think he fainted"],
    "dizzy": ["i feel dizzy", "he's dizzy and pale", "dizzy and sick"],
    "allergic": ["he's having an allergic reaction", "she's allergic to bees", "allergic to bee stings"],
    "hives": ["she has hives all over", "hives and a swollen face"],
    "wheezing": ["he's wheezing", "wheezing and can't talk"],
    "epipen": ["give her the epipen", "where is the epipen", "he has an epipen"],
    "seizure": ["he's having a seizure", "she had a seizure", "seizure and not waking up"],
    "diabetic": ["he's diabetic", "diabetic and confused", "she's a diabetic"],
    "sugar": ["his sugar is low", "low blood sugar", "give him sugar"],
    "unconscious": ["he's unconscious", "she went unconscious", "unconscious and breathing"],
    "unresponsive": ["he's unresponsive", "unresponsive and not breathing"],
    "pulse": ["i can't find a pulse", "is there a pulse", "weak pulse"],
    "cpr": ["start cpr", "should i do cpr", "doing cpr now"],
    "tourniquet": ["put on a tourniquet", "do i need a tourniquet", "tourniquet on his leg"],
    "pressure": ["put pressure on it", "keep pressure on it", "pressure is not working"],
    "concussion": ["i think he has a concussion", "concussion from the fall"],
    "sprained": ["i sprained my ankle", "sprained wrist"],
    "broken": ["i think it's broken", "broken leg", "his arm is broken"],
    "splint": ["make a splint", "do i need a splint"],
    "dislocated": ["my shoulder is dislocated", "dislocated finger"],
    "burned": ["i burned my hand", "he burned his arm on the stove"],
    "blister": ["a big blister on my heel", "the blister popped"],
    "hypothermia": ["he has hypothermia", "shivering and cold, maybe hypothermia"],
    "frostbite": ["frostbite on her toes", "i think it's frostbite"],
    "heatstroke": ["he has heatstroke", "hot and confused, maybe heatstroke"],
    "drowning": ["he was drowning", "pulled her out of the lake drowning"],
    "vomiting": ["he keeps vomiting", "vomiting and dizzy"],
    "poison": ["he ate something poison", "poison ivy rash"],
    "stung": ["i got stung by a bee", "stung on the neck"],
    "rattlesnake": ["a rattlesnake bit him", "bitten by a rattlesnake"],
    "tick": ["a tick on my leg", "how do i get a tick out"],
    "nosebleed": ["i have a nosebleed", "his nosebleed won't stop"],
    "swollen": ["my ankle is swollen", "her face is swollen"],
    "numb": ["my fingers are numb", "numb and tingling"],
    "confused": ["he's confused", "confused and sleepy"],
    "pale": ["she's pale and sweaty", "very pale"],
    "blue": ["his lips are blue", "fingers turning blue"],
}


def coverage(texts: list[str]) -> dict[str, int]:
    """How often each COVER / life word occurs in the recorded texts (Charlie's own clips)."""
    n = {w: 0 for w in COVER}
    for t in texts:
        for w in lx.tokenize(t):
            if w in n:
                n[w] += 1
    return n

# (question shown, slot class or explicit answers) -> short answers to say
QUESTIONS = [
    ("Where is it?", ["my left arm", "right leg", "my ankle", "the back of my head", "his chest", "left hand",
                      "my knee", "her neck", "lower back", "my wrist", "the shin", "my right foot", "his face",
                      "my stomach", "the thigh", "my shoulder", "left eye", "my finger", "his hip", "the forearm"]),
    ("What happened?", ["i fell", "cut it on a knife", "burned it", "a snake bit me", "bee sting", "twisted it",
                        "hit by a rock", "slipped on ice", "fell off a cliff", "a dog bit him", "crushed by a boulder",
                        "struck by lightning", "stabbed by a branch", "scalded with water", "rolled my ankle"]),
    ("Is it bleeding a lot?", ["yes", "no", "yes a lot", "just a little", "it's spurting", "it's oozing",
                               "not really", "it won't stop", "soaked through", "it stopped"]),
    ("Are they breathing normally?", ["yes", "no", "not breathing", "barely", "breathing fast", "gasping",
                                      "yes normally", "i don't know"]),
    ("Are they awake?", ["yes", "no", "kind of", "not responding", "confused", "passed out", "he's awake"]),
    ("Can they move it?", ["yes", "no", "a little", "it hurts too much", "can't move it", "not at all"]),
    ("What bit them?", ["a snake", "a rattlesnake", "a dog", "a spider", "a tick", "a bee", "a wasp", "a bat",
                        "a raccoon", "a scorpion"]),
    ("Pick one", ["one", "two", "three", "four", "number two", "option three", "back", "again", "home"]),
]


def build(seed: int = 7, recorded: list[str] | None = None) -> tuple[list[str], list[str]]:
    words = {r["word"] for r in lx.load_existing()}
    pairs, answers, missing = [], [], set()
    n = coverage(recorded or [])
    gaps = [w for w, k in sorted(n.items(), key=lambda x: x[1]) if k < COVER_MIN]
    first = [s for w in gaps for s in COVER[w]] + PAIRS[("asthma", "as my")] + PAIRS[("fell", "fall")]
    for s in first:
        missing |= {w for w in lx.tokenize(s) if w not in words}
    for sents in PAIRS.values():
        for s in sents:
            missing |= {w for w in lx.tokenize(s) if w not in words}
            pairs.append(s)
    for q, ans in QUESTIONS:
        for a in ans:
            missing |= {w for w in lx.tokenize(a) if w not in words}
            answers.append(f"{q}|{a}")
    if missing:
        print("not in lexicon (will decode as <unk>):", sorted(missing))
    rng = random.Random(seed)
    rng.shuffle(pairs); rng.shuffle(answers)
    rng.shuffle(first)
    seen = set(first)
    pairs = first + [p for p in pairs if p not in seen]   # word gaps first, then the confusion pairs
    if gaps:
        print(f"recorded fewer than {COVER_MIN} times, asked first: {', '.join(gaps)}")
    return pairs, answers


def from_pages(yaml_path, per_page: int = 3, seed: int = 9) -> list[str]:
    """ANSWERS prompts from the router's per-page lists (intake_wordlists.yaml, PC only: tile
    labels come from the personal manuals, so prompts built this way must NOT be committed).
    For each page: up to `per_page` of its tiles as the answer, the slot name as the question."""
    import yaml
    from pathlib import Path
    rng = random.Random(seed)
    pages = yaml.safe_load(Path(yaml_path).expanduser().read_text()) or {}
    out = []
    for tree, cards in pages.items():
        for card, c in (cards or {}).items():
            c = c or {}
            tiles = [str(t) for t in (c.get("tiles") or [])]
            q = str(c.get("slot") or card).replace("_", " ")
            q = q[:1].upper() + q[1:] + "?"
            rng.shuffle(tiles)
            for t in tiles[:per_page]:
                out.append(f"{q}|{t.lower()}")
            if tiles:
                out.append(f"{q}|number {rng.randint(1, min(4, len(tiles)))}")
    rng.shuffle(out)
    return out


def _join(parts: list[str], rng) -> str:
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return rng.choice(["{} and {}", "{}, {}", "{} {}"]).format(*parts)
    return rng.choice(["{}, {} and {}", "{}, {}, {}"]).format(*parts)


def from_form(yaml_path, combos_per_tree: int = 12, seed: int = 11, cap: int = 3900) -> list[str]:
    """FORM prompts for the one-screen fill-in-the-blanks (PC only, never commit the output).
    1) every tile of every blank, once, as its own answer ("Where?|left leg");
    2) a number answer per blank ("Where?|number 2");
    3) combinations: 2-3 blanks of the same tree answered in one breath, as on the form screen
       ("Fill in|left leg and spider"), up to combos_per_tree per tree, no repeats.
    Life-threat answers are not special-cased; they come in like any other tile."""
    import yaml
    from pathlib import Path
    rng = random.Random(seed)
    pages = yaml.safe_load(Path(yaml_path).expanduser().read_text()) or {}
    singles, numbers, combos = [], [], []
    for tree, cards in pages.items():
        blanks = []
        for card, c in (cards or {}).items():
            c = c or {}
            tiles = list(dict.fromkeys(str(t).lower() for t in (c.get("tiles") or []) if str(t).strip()))
            if not tiles:
                continue
            q = str(c.get("slot") or card).replace("_", " ")
            q = q[:1].upper() + q[1:] + "?"
            blanks.append((q, tiles))
            singles += [f"{q}|{t}" for t in tiles]
            numbers.append(f"{q}|number {rng.randint(1, min(4, len(tiles)))}")
        if len(blanks) < 2:
            continue
        seen = set()
        for _ in range(combos_per_tree * 4):
            if len(seen) >= combos_per_tree:
                break
            k = rng.choice([2, 2, 3]) if len(blanks) >= 3 else 2
            pick = rng.sample(blanks, k)
            said = _join([rng.choice(t) for _, t in pick], rng)
            if said not in seen:
                seen.add(said)
                combos.append(f"Fill in: {', '.join(q[:-1].lower() for q, _ in pick)}|{said}")
    for xs in (singles, numbers, combos):
        rng.shuffle(xs)
    # interleave so any stopping point has all three kinds
    out, i = [], 0
    while (singles or combos or numbers) and len(out) < cap:
        for xs in (singles, combos, singles, combos, numbers):
            if xs and len(out) < cap:
                out.append(xs.pop())
    return out


def append(path, pages=None, form=False, recorded: list[str] | None = None) -> None:
    keep = [l for l in path.read_text().splitlines() if l[:2] in ("T|", "E|")]
    pairs, answers = build(recorded=recorded)
    if pages:
        answers = (from_form(pages) if form else from_pages(pages)) + [a for a in answers if a.startswith("Pick one|")]
        print("ANSWERS from the router's pages: this prompts.txt is PC-only, do not commit it")
    path.write_text("\n".join(keep + [f"P|{s}" for s in pairs] + [f"A|{s}" for s in answers]) + "\n")
    print(f"{path}: kept {len(keep)} T/E lines, added {len(pairs)} PAIRS and {len(answers)} ANSWERS prompts")
