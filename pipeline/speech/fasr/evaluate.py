"""Score a trained model on held-out clips, and write its n-best for the router.

    python -m fasr evaluate --run firstaid --set $WORK/speech/tts/eval.jsonl [--set $WORK/speech/me/eval.jsonl]

Per noise condition: word error rate over lexicon words (words outside the lexicon count as
<unk>), how often the right words are anywhere in the 5-best, reject score on off-topic
sentences, and decode time. Writes <run>/nbest-<set>.jsonl (voice_hyp_t shape plus the
reference text) so the router thread can score end to end with the exact same inputs, and
so MultiNet's bench_loop results can be lined up clip by clip.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch
import yaml

from . import lexicon as lx
from .data import load_wav
from .decode import Params, Trie, nbest
from .features import logmel
from .model import AcousticModel
from .train import edit_distance

WORK = Path(os.environ.get("WORK", "/mnt/data/first-aid-gpu"))


def load_model(run: str, which: str = "best.pt") -> AcousticModel:
    d = torch.load(WORK / "speech" / "runs" / run / which, map_location="cpu")
    m = AcousticModel(d.get("size", "s"))
    m.load_state_dict(d["model"])
    return m.eval()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fasr evaluate")
    ap.add_argument("--run", required=True)
    ap.add_argument("--set", action="append", required=True)
    ap.add_argument("--beam", type=int, default=24)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)
    rows = yaml.safe_load(lx.LEXICON.read_text())["words"]
    ids = {r["word"]: r["id"] for r in rows}
    trie = Trie.from_lexicon(rows)
    model = load_model(a.run)
    reject_texts = {" ".join(lx.tokenize(s)) for s in
                    (yaml.safe_load((lx.CONTENT / "voice" / "topics.yaml").read_text()) or {}).get("reject", {}).get("test", [])}
    report = {}
    for sp in a.set:
        sp = Path(sp)
        recs = [json.loads(l) for l in sp.read_text().splitlines() if l.strip()]
        if a.limit:
            recs = recs[: a.limit]
        out = (WORK / "speech" / "runs" / a.run / f"nbest-{sp.parent.name}-{sp.stem}.jsonl").open("w")
        agg = defaultdict(lambda: defaultdict(float))
        for r in recs:
            wav = Path(r["wav"]) if Path(r["wav"]).is_absolute() else sp.parent / r["wav"]
            f = torch.from_numpy(logmel(load_wav(str(wav))))[None]
            t0 = time.time()
            with torch.no_grad():
                lp = model(f)[0].numpy()
            hyp = nbest(lp, trie, Params(beam=a.beam))
            dt = time.time() - t0
            ref = [ids.get(w, 0) for w in lx.tokenize(r["text"])]
            cond = r.get("noise", "clean")
            off = " ".join(lx.tokenize(r["text"])) in reject_texts
            g = agg[cond]
            g["n"] += 1; g["secs"] += dt
            if off:
                g["off_n"] += 1; g["off_reject"] += hyp["reject"]
            else:
                top = hyp["h"][0]["ids"] if hyp["n"] else []
                g["err"] += edit_distance(top, ref); g["words"] += len(ref)
                g["in5"] += any(h["ids"] == ref for h in hyp["h"])
                g["on_n"] += 1; g["on_reject"] += hyp["reject"]
            out.write(json.dumps({"wav": str(wav), "text": r["text"], "noise": cond, "ref": ref, **hyp}) + "\n")
        res = {}
        for cond, g in sorted(agg.items()):
            res[cond] = {
                "clips": int(g["n"]),
                "wer": round(g["err"] / max(1, g["words"]), 3),
                "exact_in_5best": round(g["in5"] / max(1, g["on_n"]), 3),
                "reject_on_topic": round(g["on_reject"] / max(1, g["on_n"]), 1),
                "reject_off_topic": round(g["off_reject"] / max(1, g["off_n"]), 1) if g["off_n"] else None,
                "ms_per_clip_pc": round(1000 * g["secs"] / max(1, g["n"]), 1),
            }
        report[str(sp)] = res
        print(sp, json.dumps(res, indent=1), flush=True)
    (WORK / "speech" / "runs" / a.run / "eval.json").write_text(json.dumps(report, indent=1))
