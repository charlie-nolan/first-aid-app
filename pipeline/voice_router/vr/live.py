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
from .form import (MAX_TREES, Candidate, answer, first_sentence, open_fields, ranked_cards,
                   split_tabs, _life_slots)
from .intake import fill, fill_nbest


def load_all(tree_dirs: list[Path], slots_dir: Path) -> dict[str, tuple[dict, dict]]:
    out = {}
    for f in sorted(Path(slots_dir).glob("*.yaml")):
        tree = load_tree(tree_dirs, f.stem)
        smap = yaml.safe_load(f.read_text()) or {}
        if tree and smap.get("slots"):
            out[f.stem] = (tree, smap)
    return out


def keep_trees(hyps: list[str], trees: dict, k: int = MAX_TREES) -> list[Candidate]:
    """Keyword stand-in for the router: trees ranked by blanks the sentence fills."""
    scored = []
    for i, (name, (tree, smap)) in enumerate(trees.items()):
        filled = set(fill_nbest(hyps, smap)) | set(fill(hyps[0], smap["slots"], smap.get("priority")))
        if not filled:                             # a tie ("leg" vs "shin") fills no blank but is
                                                   # still evidence for the tree (board run s001)
            continue
        c = Candidate(name, tree, smap)
        life = _life_slots(c)
        scored.append((sum(2 if s in life else 1 for s in filled), -i, name))
    scored.sort(reverse=True)
    top = scored[:k]
    tot = sum(s for s, _, _ in top) or 1
    return [Candidate(n, *trees[n], p=s / tot) for s, _, n in top]


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
        from fasr.decode import Params, Trie, life_bias, nbest
        from fasr.evaluate import load_model
        from fasr.features import logmel
        self.torch, self.load_wav, self.logmel, self.nbest, self.Params = torch, load_wav, logmel, nbest, Params
        rows = yaml.safe_load(lx.LEXICON.read_text())["words"]
        self.words = {r["id"]: r["word"] for r in rows}
        self.trie, self.bias = Trie.from_lexicon(rows), life_bias(rows)
        self.model = load_model(run)

    def __call__(self, wav: Path) -> tuple[list[str], int]:
        with self.torch.no_grad():
            lp = self.model(self.torch.from_numpy(self.logmel(self.load_wav(str(wav))))[None])[0].numpy()
        res = self.nbest(lp, self.trie, self.Params(bias=self.bias))
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
    ap.add_argument("--reject", type=int, default=70, help="reject score at which a clip is asked again")
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
        if speech and reject >= a.reject:
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
            kind, name, card = answer(hyps[0], cands)
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
