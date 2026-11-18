"""Live voice test on the PC (Charlie 11-17: "stress test it with my voice now").

The device's mic, the PC's brain: hold TALK on the recorder firmware (FREE mode) and the clip
lands on the PC (`python -m fasr devrec` running); this loop decodes it with the speech model,
runs the fill-in-the-blanks intake (vr.form) and prints the screen the device would show: the
form with its open blanks, the SKIP list, or the card that opens (title + cites, never text).

    python -m vr.live --trees TREES_DIR [MORE_DIRS] --slots SLOTS_DIR \\
        [--clips $WORK/speech/me_dev/free --fasr ~/first-aid-speech/pipeline/speech --run firstaid]

Without --clips it reads typed lines instead (same engine, no speech), handy to try wording.
Each turn is also written to a log (--log, default live-<date>.jsonl beside the clips or here):
what was heard, which trees were kept, what the screen showed. Commands: "new" (or BACK clip
"back" / "home") starts a new problem; "multi" before a sentence = TALK+WIN_R (tabs).

Tree choice: the trained router is not wired in yet (phase 2), so trees are kept by keywords:
every tree's blanks are filled from the sentence and the 3 trees that fill most (life-threat
blanks count double) stay. Say so when reading results.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

import yaml

from .data import load_tree
from .form import (_rule_hit, MAX_TREES, named_trees, names_tree, topic_words, _key, Candidate, answer, first_sentence, open_fields, ranked_cards,
                   split_tabs, _life_slots, _rule_hit)
from .intake import UNIVERSAL, _stem, fill, fill_nbest, slots_of, tokenize

GENERIC_HOME = {"wfa-cpr"}   # the tree that owns the shared life blanks (smap generic_home: true adds one)


def load_all(tree_dirs: list[Path], slots_dir: Path) -> dict[str, tuple[dict, dict]]:
    out = {}
    for f in sorted(Path(slots_dir).glob("*.yaml")):
        tree = load_tree(tree_dirs, f.stem)
        smap = yaml.safe_load(f.read_text()) or {}
        if tree and smap.get("slots"):
            out[f.stem] = (tree, smap)
    return out


def _heard_topic(hyps: list[str], name: str, tree: dict, smap: dict) -> bool:
    return bool(hyps) and names_tree(tokenize(hyps[0]), name, tree, smap)


LOCATION_SLOTS = {"body_part", "side", "location"}
INJURY_WORDS = {"broke", "broken", "break", "snap", "snapped", "crack", "cracked", "crooked", "bent",
                "deformed", "twisted", "twist", "rolled", "sprained", "dislocated", "whacked", "hit",
                "blow", "bump", "bumped", "smashed", "crushed", "fell", "fall", "landed", "stubbed",
                "jammed", "hurt", "hurts", "pain", "painful", "sore", "swollen", "swelling", "bruised",
                "cut", "stake", "poked", "stabbed", "impaled", "stuck", "tripped", "trip", "slipped",
                "ballooning", "limping", "limp", "holding", "grabs", "grabbing", "clutching", "lift",
                "step", "weight"}
# df95add PC check: a place-only rule without these sent 51 real injuries ("i think i broke my
# leg", "rolled my ankle", "blow to the head") to the start screen; only a bare place ("toe",
# "my toe", "his leg") should.


def _weight(slot, value, life, home) -> int:
    """How much one filled blank says about a tree. The shared blanks (awake, breathing) count only
    for their home tree (CPR), and only with their life value: "breathing fine", "awake, alert" is
    no evidence for CPR (38b1139 scene check: 18 calm scenes jumped to wfa-cpr)."""
    if slot in UNIVERSAL:
        return 2 if home and value in UNIVERSAL[slot] else 0
    return 2 if slot in life else 1


def _handles_life(c: Candidate, vals: dict) -> bool:
    """This tree heard its own problem AND has its own rule for the shared life values heard
    ("stove fumes, she's passed out and not breathing": wfa-co's conscious-no rule goes to
    treat_now, whose first step is protect yourself / get her out, then CPR). Trees thread 11-18:
    a named tree's own life rule beats the generic CPR jump. Rules on the tree's own blanks only
    (bleeding) do not count here: "bleeding and not breathing" still ranks CPR first."""
    c.filled = dict(vals)
    go = _rule_hit(c)
    if not go:
        return False
    lt = c.smap.get("life_threat") or []
    rules = [{"if": {s: v}, "go": g} for s, m in lt.items() for v, g in m.items()] if isinstance(lt, dict) else lt
    if "/" in go and go.split("/")[0] in GENERIC_HOME:
        return False                               # the shared "not breathing -> wfa-cpr/cpr" rule many
                                                   # trees carry is CPR's, not the tree's own handling
                                                   # (trees 11-18: bites/burns/improvised_gear outranked
                                                   # wfa-bleeding on "bleeding a lot and not breathing")
    return any(r["go"] == go and any(s in UNIVERSAL for s in r["if"]) for r in rules)


def _shared_slots(trees: dict) -> set:
    """Blanks that say nothing about WHICH tree: the CPR tree's own (cause, breathing...) and any
    blank three or more trees carry (bleeding_type). Hearing one is not a tree's own problem
    (31298ea PC check: "bleeding a lot and not breathing" opened wfa-co/treat_now; cold
    not-breathing scenes ranked wfa-co / wfa-drowning above CPR)."""
    count: dict = {}
    for _, m in trees.values():
        for slot in (m.get("slots") or {}):
            count[slot] = count.get(slot, 0) + 1
    home = {slot for n in GENERIC_HOME if n in trees for slot in (trees[n][1].get("slots") or {})}
    return home | {slot for slot, k in count.items() if k >= 3}


def keep_trees(hyps: list[str], trees: dict, k: int = MAX_TREES) -> list[Candidate]:
    """Keyword stand-in for the router, in two tiers: trees named outright ("choking", "carbon
    monoxide", smap topic_words) first, then by blanks the sentence fills (life-threat blanks count
    double; naming adds 3 to the share shown)."""
    scored = []
    words = tokenize(hyps[0]) if hyps else []
    heard = named_trees(words, trees)
    hurt = bool(set(words) & INJURY_WORDS)
    shared = _shared_slots(trees)
    for i, (name, (tree, smap)) in enumerate(trees.items()):
        vals = {**fill(hyps[0], slots_of(smap), smap.get("priority")), **fill_nbest(hyps, smap)}
        named = name in heard
        if not vals and not named:                 # a tie ("leg" vs "shin") fills no blank but is
            continue                               # still evidence for the tree (board run s001)
        c = Candidate(name, tree, smap)
        life = _life_slots(c)
        home = name in GENERIC_HOME or smap.get("generic_home")
        if home and not any(s in UNIVERSAL and v in UNIVERSAL[s] for s, v in vals.items()):
            vals = {}                              # CPR ranks only on a life value; its own "cause"
        score = sum(_weight(s, v, life, home) for s, v in vals.items())   # alone (hypothermia, drowning)
        loc = smap.get("location_slots") or LOCATION_SLOTS
        real = named or (score > 0 and (hurt or any(s not in loc for s in vals)))
        own = named or any(s not in loc and s not in UNIVERSAL and s not in shared for s in vals)
        handles = not home and own and _handles_life(c, vals)
        scored.append((named or handles, score + 3 * named + 3 * handles, -i, name, real))
    if not any(x[4] for x in scored):              # only a place was heard ("toe", "my toe"): no problem
        return []                                  # yet, so no card; start screen (81fe3ed "toe in")
    scored = [x[:4] for x in scored]
    if any(sc for _, sc, _, _ in scored):          # "he's not breathing": the shared life blanks
        scored = [x for x in scored if x[1]]       # (awake, breathing) rank only their home tree,
    scored.sort(reverse=True)                      # CPR, not every tree that has them (1623cdb check).
                                                   # Tiered (Charlie 11-18): a tree named outright
                                                   # ("carbon monoxide", "choking") ranks above trees
                                                   # that only share a word ("stove", "dirty").
    top = scored[:k]
    tot = sum(s for _, s, _, _ in top) or 1
    return [Candidate(n, *trees[n], p=s / tot) for _, s, _, n in top]


def rescue(hyps: list[str], trees: dict, cands: list[Candidate]):
    """Before a later answer is read against the open form: does it bring a life threat or name a
    problem the kept trees do not cover? (Board run s007: after a misheard first sentence kept burns
    and wounds, "choking", "can't breathe" were read as burns answers and never reached choking.)
    The kept trees read the words first (s007 replay: "can't breathe" must not jump from the kept
    choking tree to allergy). In order:
      1. a tree not kept is named outright ("choking"): restart the form from these words;
      2. a kept tree meets a life rule with these words, or is named in them: an answer (None);
      3. a kept tree fills any blank from them: an answer (None);
      4. a tree not kept meets a life rule with these words alone: open that card.
    Returns first_sentence's result (and replaces cands), or None to read the words as an answer."""
    kept = {c.name for c in cands}
    if hyps and named_trees(tokenize(hyps[0]), trees) - kept:
        new = keep_trees(hyps, trees)              # a condition named outright wins over a kept tree's
        if new:                                    # generic rule (s009: "choking unresponsive" must open
            cands[:] = new                         # choking/unconscious, not head-spine's cpr/start)
            return first_sentence(hyps, cands)
    heard = named_trees(tokenize(hyps[0]), trees) if hyps else set()
    for c in cands:
        probe = Candidate(c.name, c.tree, c.smap)
        probe.filled = {**c.filled, **fill_nbest(hyps, c.smap)}
        if _rule_hit(probe) or c.name in heard:
            return None
    if any(fill_nbest(hyps, c.smap) for c in cands):
        return None
    for name, (tree, smap) in trees.items():
        if name in kept:
            continue
        c = Candidate(name, tree, smap)
        c.filled = fill_nbest(hyps, smap)
        if _rule_hit(c):
            cands[:] = [Candidate(name, tree, smap)]
            return first_sentence(hyps, cands)
    return None


def _title(trees, name, card):
    if card not in trees[name][0]["nodes"] and "/" in card:   # a life rule's card in another tree
        name, card = card.split("/", 1)
    if name not in trees:
        return card, []
    tree = trees[name][0]
    node = tree["nodes"].get(card, {})
    return f"{tree.get('title', name)} > {node.get('label', card)}", node.get("cite") or []


def show_form(cands, trees) -> list[str]:
    lines = []
    for f in open_fields(cands):
        choices = " | ".join(str(c.get("label", c.get("value"))) for c in f.choices)
        dep = f"   (after {f.when[0]}={f.when[1]})" if f.when else ""
        lines.append(f"  [ ] {f.question}:  {choices}{dep}")
    heard = {k: v for c in cands for k, v in c.filled.items()}
    if heard:
        lines.insert(0, "  heard: " + ", ".join(f"{k}={v}" for k, v in heard.items()))
    return lines


def show_open(trees, name, card) -> list[str]:
    t, cites = _title(trees, name, card)
    return [f"  >>> CARD OPENS: {t}", f"      cites: {', '.join(map(str, cites)) or '-'}"]


def show_skip(trees, cards) -> list[str]:
    return [f"  {i + 1}. {_title(trees, n, c)[0]}" for i, (n, c) in enumerate(cards[:6])]


class Speech:
    """fasr model + decoder from the speech worktree (PR #17), loaded once."""

    def __init__(self, fasr_root: Path, run: str):
        sys.path.insert(0, str(Path(fasr_root).expanduser()))
        import torch
        from fasr import lexicon as lx
        from fasr.data import load_wav
        from fasr.decode import Params, Trie, decode, life_bias
        from fasr.evaluate import WORK, load_model
        from fasr.features import logmel
        self.torch, self.load_wav, self.logmel, self.decode, self.Params = torch, load_wav, logmel, decode, Params
        rows = yaml.safe_load(lx.LEXICON.read_text())["words"]
        self.words = {r["id"]: r["word"] for r in rows}
        self.trie, self.bias = Trie.from_lexicon(rows), life_bias(rows)
        self.model = load_model(run)
        cal = WORK / "speech" / "runs" / run / "reject_calib.json"   # from `fasr calibrate`
        self.calib = json.loads(cal.read_text()) if cal.exists() else None
        if not self.calib:
            print(f"(no {cal}: using the raw fit-gap reject)", flush=True)

    def __call__(self, wav: Path) -> tuple[list[str], int]:
        with self.torch.no_grad():
            lp = self.model(self.torch.from_numpy(self.logmel(self.load_wav(str(wav))))[None])[0].numpy()
        res = self.decode(lp, self.trie, self.Params(bias=self.bias), self.calib)   # calibrated reject
        hyps = [" ".join(self.words.get(i, "?") for i in h["ids"] if i) for h in res["h"]]
        return hyps or [""], res["reject"]


def clips(folder: Path, poll: float = 0.5):
    seen = {p.name for p in folder.glob("*.wav")}
    print(f"(waiting for clips in {folder}: hold TALK on the board in FREE mode)", flush=True)
    while True:
        new = sorted(p for p in folder.glob("*.wav") if p.name not in seen)
        for p in new:
            time.sleep(0.3)                        # let devrec finish writing
            seen.add(p.name)
            yield p
        time.sleep(poll)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vr.live")
    ap.add_argument("--trees", type=Path, nargs="+", required=True)
    ap.add_argument("--slots", type=Path, required=True, help="folder of <tree>.yaml slot files")
    ap.add_argument("--clips", type=Path, help="folder devrec writes FREE clips to; omit to type")
    ap.add_argument("--fasr", type=Path, help="speech package root (holds fasr/)")
    ap.add_argument("--run", default="firstaid", help="speech model run")
    ap.add_argument("--log", type=Path)
    ap.add_argument("--reject", type=int, default=50,
                    help="calibrated reject above which a clip is asked again (same as the tether bridge)")
    a = ap.parse_args(argv)
    trees = load_all(a.trees, a.slots)
    print(f"{len(trees)} trees with blanks loaded")
    speech = Speech(a.fasr, a.run) if a.clips else None
    log = a.log or ((a.clips or Path.cwd()) / f"live-{dt.date.today().isoformat()}.jsonl")
    src = clips(a.clips) if a.clips else (l.rstrip("\n") for l in sys.stdin)
    cands, multi, turn = None, False, 0
    if not a.clips:
        print("type the first sentence (\"new\" = start over, \"multi\" = TALK+WIN_R next):")
    for item in src:
        if speech:
            hyps, reject = speech(item)
            print(f"\n[{item.name}] heard: {hyps[0]!r}  (reject {reject}, {len(hyps)} guesses)")
        else:
            hyps, reject = [item], 0
        if speech and reject > a.reject:
            print(f"  (not sure what was said, reject {reject}: the device would ask again)")
            _log(log, {"t": time.time(), "hyps": hyps, "reject": reject, "clip": str(item), "screen": "again"})
            continue
        words = hyps[0].strip().lower()
        if words in ("new", "home", "back") or not words:
            cands, turn = None, 0
            print("--- new problem ---")
            continue
        if words == "multi":
            multi = True
            print("(next sentence = several problems)")
            continue
        turn += 1
        rec = {"t": time.time(), "turn": turn, "hyps": hyps, "reject": reject,
               "clip": str(item) if speech else None}
        if cands is None:
            cands = keep_trees(hyps, trees)
            rec["kept"] = [(c.name, round(c.p, 2)) for c in cands]
            print("  trees kept: " + (", ".join(f"{c.name} {c.p:.0%}" for c in cands) or "none"))
            if not cands:
                print("  no blanks heard in any tree: the device would show the Injury | Survival tiles")
                cands, turn = None, 0
                rec["screen"] = "start"
                _log(log, rec)
                continue
            kind, name, card = first_sentence(hyps, cands, multi=multi)
            multi = False
        else:
            r = rescue(hyps, trees, cands)          # a life threat / new problem named mid-form
            kind, name, card = r if r else answer(hyps[0], cands)
        rec["screen"] = kind
        if kind == "open":
            out = show_open(trees, name, card)
            rec["card"] = f"{name}/{card}"
            cands = None
        elif kind == "skip":
            out = ["  SKIP: likely cards"] + show_skip(trees, card)
            rec["cards"] = [f"{n}/{c}" for n, c in card]
        elif kind == "tabs":
            out = []
            for i, tab in enumerate(split_tabs(cands)[:5]):
                out += [f"  TAB {i + 1}: {', '.join(c.name for c in tab)}"] + show_form(tab, trees)
        else:
            out = [f"  FORM (turn {turn}):"] + show_form(cands, trees)
            top = ranked_cards(cands, 3)
            out += ["  top 3 if you SKIP now: " + "; ".join(_title(trees, n, c)[0] for n, c in top)]
            rec["fields"] = [f.key for f in open_fields(cands)]
            rec["top3"] = [f"{n}/{c}" for n, c in top]
        print("\n".join(out), flush=True)
        _log(log, rec)


def _log(path: Path, rec: dict):
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")


if __name__ == "__main__":
    main()
