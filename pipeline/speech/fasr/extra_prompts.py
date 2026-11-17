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
}

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


def build(seed: int = 7) -> tuple[list[str], list[str]]:
    words = {r["word"] for r in lx.load_existing()}
    pairs, answers, missing = [], [], set()
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
    return pairs, answers


def append(path) -> None:
    keep = [l for l in path.read_text().splitlines() if l[:2] in ("T|", "E|")]
    pairs, answers = build()
    path.write_text("\n".join(keep + [f"P|{s}" for s in pairs] + [f"A|{s}" for s in answers]) + "\n")
    print(f"{path}: kept {len(keep)} T/E lines, added {len(pairs)} PAIRS and {len(answers)} ANSWERS prompts")
