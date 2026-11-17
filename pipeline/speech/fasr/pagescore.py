"""Hard per-page word lists vs soft context boost vs the full list, scored per tree.

    python -m fasr pagescore --run firstaid --set $WORK/speech/me_dev/eval.jsonl \
        --pages ~/first-aid-voice/out/intake_wordlists.yaml --gold gold.jsonl

--pages: {tree: {card: {slot, tiles, words}}} (router's intake_wordlists.yaml; stays off git).
--gold:  one line per clip, {"text": <prompt text as in the manifest>, "tree": <tree id>}; clips
         with no tree (off-topic) are skipped. Each clip is decoded three ways with the words of
         its gold tree's pages (union): full list, hard subset (+ reserved 0-9), soft context bonus.
Reports, per mode: answer-word recall (top-1 and 5-best) for words in that tree's lists, wrong
tree words per clip, and WER.
--fields (form screen, instead of --gold): one line per clip, {"text": ..., "fields": ["tree/card", ...]}
         = the open fields from vr.form.open_fields; the clip's list is the union of those cards' words
         (+ reserved 0-9: numbers, yes/no, back/again/home). Writes $WORK/speech/runs/<run>/pagescore.json.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import torch
import yaml

from . import lexicon as lx
from .data import load_wav
from .decode import Params, Trie, context_bias, life_bias, nbest
from .evaluate import WORK, load_model
from .features import logmel
from .train import edit_distance


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fasr pagescore")
    ap.add_argument("--run", required=True)
    ap.add_argument("--set", required=True)
    ap.add_argument("--pages", required=True)
    ap.add_argument("--gold", help="text -> tree (whole-tree union)")
    ap.add_argument("--fields", help="text -> open form fields tree/card (union of those cards)")
    ap.add_argument("--beam", type=int, default=24)
    ap.add_argument("--bonus", type=float, default=0.5, help="soft context bonus (nats per word)")
    ap.add_argument("--garbage-enter", type=float, default=-2.0, help="hard subset: easier <unk> for out-of-list sounds")
    ap.add_argument("--garbage-phone", type=float, default=-1.0)
    a = ap.parse_args(argv)
    if not (a.gold or a.fields):
        ap.error("give --gold or --fields")

    rows = yaml.safe_load(lx.LEXICON.read_text())["words"]
    ids = {r["word"]: r["id"] for r in rows}
    pages = yaml.safe_load(Path(a.pages).expanduser().read_text()) or {}
    card_words = {}
    for tree, cards in pages.items():
        for card, c in (cards or {}).items():
            ws = set()
            for w in (c or {}).get("words", []) or []:
                ws |= set(lx.tokenize(str(w)))
            card_words[f"{tree}/{card}"] = ws
    # key -> word set; gold maps clip text -> key
    tree_words, gold = {}, {}
    if a.fields:
        for l in Path(a.fields).expanduser().read_text().splitlines():
            if l.strip():
                g = json.loads(l)
                fs = tuple(sorted(f for f in g.get("fields") or [] if f in card_words))
                if fs:
                    key = "+".join(fs)
                    tree_words[key] = set().union(*[card_words[f] for f in fs])
                    gold[" ".join(lx.tokenize(g["text"]))] = key
    else:
        for k, ws in card_words.items():
            tree_words.setdefault(k.split("/", 1)[0], set()).update(ws)
        for l in Path(a.gold).expanduser().read_text().splitlines():
            if l.strip():
                g = json.loads(l)
                gold[" ".join(lx.tokenize(g["text"]))] = g.get("tree")
    full_trie = Trie.from_lexicon(rows)
    life = life_bias(rows)
    model = load_model(a.run)
    agg = {m: defaultdict(float) for m in ("full", "hard", "soft")}
    tries = {}
    for line in Path(a.set).read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        tree = gold.get(" ".join(lx.tokenize(r["text"])))
        if not tree or tree not in tree_words:
            continue
        tw = tree_words[tree]
        tw_ids = {ids[w] for w in tw if w in ids}
        if tree not in tries:
            sub = [x for x in rows if x["id"] < 10 or x["word"] in tw]
            tries[tree] = Trie.from_lexicon(sub)
        wav = Path(r["wav"]) if Path(r["wav"]).is_absolute() else Path(a.set).parent / r["wav"]
        with torch.no_grad():
            lp = model(torch.from_numpy(logmel(load_wav(str(wav))))[None])[0].numpy()
        ref = [ids.get(w, 0) for w in lx.tokenize(r["text"])]
        want = [w for w in ref if w in tw_ids]
        runs = {
            "full": nbest(lp, full_trie, Params(beam=a.beam, bias=life)),
            "hard": nbest(lp, tries[tree], Params(beam=a.beam, bias=life, garbage_enter=a.garbage_enter,
                                                  garbage_phone=a.garbage_phone)),
            "soft": nbest(lp, full_trie, Params(beam=a.beam, bias=context_bias(rows, tw, a.bonus))),
        }
        for m, hyp in runs.items():
            g = agg[m]
            top = hyp["h"][0]["ids"] if hyp["n"] else []
            any5 = set().union(*[set(h["ids"]) for h in hyp["h"]]) if hyp["n"] else set()
            g["clips"] += 1
            g["err"] += edit_distance(top, ref); g["words"] += len(ref)
            g["want"] += len(want)
            g["hit1"] += sum(w in top for w in want)
            g["hit5"] += sum(w in any5 for w in want)
            g["false"] += len({w for w in top if w in tw_ids} - set(want))
    res = {m: {"clips": int(g["clips"]),
               "answer_recall_top1": round(g["hit1"] / max(1, g["want"]), 3),
               "answer_recall_5best": round(g["hit5"] / max(1, g["want"]), 3),
               "wrong_answer_words_per_clip": round(g["false"] / max(1, g["clips"]), 3),
               "wer": round(g["err"] / max(1, g["words"]), 3)} for m, g in agg.items()}
    out = WORK / "speech" / "runs" / a.run / ("formscore.json" if a.fields else "pagescore.json")
    out.write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
