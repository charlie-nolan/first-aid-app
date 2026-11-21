"""Export a trained model for the ESP32-S3: ONNX (float) + golden files for the C port.

    python -m fasr export --run firstaid

Writes $WORK/speech/runs/<run>/export/:
  model.onnx          float32, input (1, T, 40) log-mel, output (1, T', 41) log-probs
  calib.npy           ~200 feature clips for int8 calibration
  golden/*.npz        audio -> features -> log-probs -> n-best, for C parity tests
Next step (desktop, needs esp-ppq): quantise model.onnx to int8 .espdl with calib.npy,
then the firmware's fasr component loads it from the voice model partition. Size and
speed on the real chip are measured there, not guessed here.
"""
from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path

import numpy as np
import torch
import yaml

from . import lexicon as lx
from .data import load_wav
from .decode import Trie, nbest
from .evaluate import load_model
from .features import logmel

WORK = Path(os.environ.get("WORK", "/mnt/data/first-aid-gpu"))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fasr export")
    ap.add_argument("--run", required=True)
    ap.add_argument("--calib", default=str(WORK / "speech" / "tts" / "dev.jsonl"))
    a = ap.parse_args(argv)
    run = WORK / "speech" / "runs" / a.run
    out = run / "export"
    (out / "golden").mkdir(parents=True, exist_ok=True)
    m = load_model(a.run)
    torch.onnx.export(m, torch.zeros(1, 150, 40), out / "model.onnx", input_names=["feats"],
                      output_names=["logp"], dynamic_axes={"feats": {1: "T"}, "logp": {1: "T2"}}, opset_version=13,
                      dynamo=False)
    cp = Path(a.calib)
    recs = [json.loads(l) for l in cp.read_text().splitlines() if l.strip()]
    random.Random(0).shuffle(recs)
    feats = []
    rows = yaml.safe_load(lx.LEXICON.read_text())["words"]
    trie = Trie.from_lexicon(rows)
    for i, r in enumerate(recs[:200]):
        wav = Path(r["wav"]) if Path(r["wav"]).is_absolute() else cp.parent / r["wav"]
        x = load_wav(str(wav))
        f = logmel(x)
        feats.append(f[:300])
        if i < 8:
            with torch.no_grad():
                lp = m(torch.from_numpy(f)[None])[0].numpy()
            np.savez(out / "golden" / f"{i}.npz", audio=(x * 32767).astype(np.int16), feats=f, logp=lp,
                     nbest=json.dumps(nbest(lp, trie)), text=r["text"])
    np.save(out / "calib.npy", np.array(feats, dtype=object), allow_pickle=True)
    n = m.n_params()
    print(f"exported {out}: {n/1e6:.2f} M weights (~{n/1e6:.2f} MB int8), {len(feats)} calibration clips")
