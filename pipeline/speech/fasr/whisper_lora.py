"""Charlie's LoRA idea: fine-tune Whisper on first-aid speech, then use it as a labelling teacher.

Whisper never goes on the device. It runs on the PC to:
  1. `label`: write down what was said in unscripted recordings (Charlie talking freely,
     real trail audio) so they become extra training data for the tiny model;
  2. `score`: give a strong reference on the eval sets, to show how much of the tiny
     model's error is the audio being hard vs. the model being small.

    python -m fasr whisper train --train $WORK/speech/tts/train.jsonl --train $WORK/speech/me/train.jsonl:4 \
        --dev $WORK/speech/tts/dev.jsonl [--base openai/whisper-small] [--hours 2]
    python -m fasr whisper label --audio $WORK/speech/me/free --out $WORK/speech/me/labelled.jsonl
    python -m fasr whisper score --set $WORK/speech/tts/eval.jsonl

LoRA (rank 16 on the attention q/v and fc1/fc2 layers) trains ~1-2% of the weights, so
whisper-small fits in ~6 GB with fp16 and batch 8. The adapter is saved in
$WORK/speech/whisper-lora/ (resumable; obeys the same pause file as fasr train).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
from pathlib import Path

import numpy as np

from . import lexicon as lx

WORK = Path(os.environ.get("WORK", "/mnt/data/first-aid-gpu"))
OUT = WORK / "speech" / "whisper-lora"
PAUSE = Path(os.environ.get("FASR_PAUSE_FILE", WORK / "PAUSE_GPU"))


def _recs(spec: str) -> list[dict]:
    path, _, w = spec.partition(":")
    p = Path(path)
    out = []
    for line in p.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            wav = Path(r["wav"]) if Path(r["wav"]).is_absolute() else p.parent / r["wav"]
            out.append({"wav": str(wav), "text": r["text"], "w": float(w or 1)})
    return out


def _prompt() -> str:
    """Initial prompt nudging Whisper toward first-aid words (helps before and after LoRA)."""
    return ("Backcountry first aid: bleeding, tourniquet, splint, sprained ankle, fracture, "
            "hypothermia, frostbite, heat stroke, burns, snake bite, bee sting, CPR, choking.")


def train(a):
    import torch
    from peft import LoraConfig, PeftModel, get_peft_model
    from transformers import WhisperForConditionalGeneration, WhisperProcessor
    from .data import Augment, load_wav

    dev = "cuda"
    proc = WhisperProcessor.from_pretrained(a.base, language="en", task="transcribe")
    model = WhisperForConditionalGeneration.from_pretrained(a.base, torch_dtype=torch.float16)
    model.config.forced_decoder_ids = None
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "adapter_config.json").exists():
        model = PeftModel.from_pretrained(model, OUT, is_trainable=True)
        print("resumed adapter from", OUT)
    else:
        cfg = LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05,
                         target_modules=["q_proj", "v_proj", "fc1", "fc2"])
        model = get_peft_model(model, cfg)
    for n, p in model.named_parameters():   # LoRA weights in fp32 for stable updates
        if p.requires_grad:
            p.data = p.data.float()
    model.print_trainable_parameters()
    model.to(dev).train()
    recs = [r for s in a.train for r in _recs(s)]
    weights = np.array([r["w"] for r in recs]); weights /= weights.sum()
    dev_recs = _recs(a.dev)[:200]
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=a.lr)
    aug = Augment()
    rng = np.random.default_rng(0)
    state_f = OUT / "state.json"
    st = json.loads(state_f.read_text()) if state_f.exists() else {"secs": 0.0, "step": 0}
    t0 = time.time(); last = t0
    budget = a.hours * 3600

    def batch(rs, augment):
        xs = [load_wav(r["wav"]) for r in rs]
        if augment:
            xs = [aug(x) for x in xs]
        f = proc.feature_extractor(xs, sampling_rate=16000, return_tensors="pt").input_features
        tok = proc.tokenizer([r["text"] for r in rs], return_tensors="pt", padding=True)
        # Whisper pads with <|endoftext|>, the same id that ends each text: mask by attention_mask, not by id,
        # or every end token is masked too and the model never learns to stop (it repeats until max tokens).
        lab = tok.input_ids.masked_fill(tok.attention_mask.eq(0), -100)
        if (lab[:, 0] == model.config.decoder_start_token_id).all():   # the model adds <|startoftranscript|> itself
            lab = lab[:, 1:]
        return f.to(dev, torch.float16), lab.to(dev)

    while st["secs"] + time.time() - t0 < budget:
        if PAUSE.exists():
            st["secs"] += time.time() - t0
            model.save_pretrained(OUT); state_f.write_text(json.dumps(st))
            print("paused: adapter saved"); model.cpu(); torch.cuda.empty_cache()
            while PAUSE.exists():
                time.sleep(15)
            model.to(dev); t0 = time.time(); print("resumed")
        rs = [recs[i] for i in rng.choice(len(recs), a.bs, p=weights)]
        f, lab = batch(rs, True)
        with torch.autocast("cuda", dtype=torch.float16):
            loss = model(input_features=f, labels=lab).loss
        opt.zero_grad(); loss.backward(); opt.step()
        st["step"] += 1
        if st["step"] % 50 == 0:
            print(f"step {st['step']} loss {loss.item():.3f}", flush=True)
        if time.time() - last > 600:
            st["secs"] += time.time() - t0; t0 = time.time(); last = t0
            model.save_pretrained(OUT); state_f.write_text(json.dumps(st))
    st["secs"] += time.time() - t0
    model.save_pretrained(OUT); state_f.write_text(json.dumps(st))
    print("done; dev WER:", score_recs(model.eval(), proc, dev_recs))


def load_teacher(base: str):
    import torch
    from peft import PeftModel
    from transformers import WhisperForConditionalGeneration, WhisperProcessor
    proc = WhisperProcessor.from_pretrained(base, language="en", task="transcribe")
    model = WhisperForConditionalGeneration.from_pretrained(base, torch_dtype=torch.float16)
    if (OUT / "adapter_config.json").exists():
        model = PeftModel.from_pretrained(model, OUT).merge_and_unload()
    else:
        print("no LoRA adapter yet: using plain", base)
    return model.to("cuda").eval(), proc


def transcribe(model, proc, xs: list[np.ndarray]) -> list[tuple[str, float]]:
    import torch
    f = proc.feature_extractor(xs, sampling_rate=16000, return_tensors="pt").input_features.to("cuda", torch.float16)
    with torch.no_grad():
        g = model.generate(f, language="en", task="transcribe", output_scores=True,
                           return_dict_in_generate=True, max_new_tokens=64)
    texts = proc.batch_decode(g.sequences, skip_special_tokens=True)
    sc = model.compute_transition_scores(g.sequences, g.scores, normalize_logits=True)
    conf = [float(np.exp(s[s > -1e4].float().mean().item())) if len(s) else 0.0 for s in sc.cpu()]
    return list(zip([t.strip().lower() for t in texts], conf))


def score_recs(model, proc, recs) -> float:
    from .data import load_wav
    from .train import edit_distance
    err = n = 0
    for i in range(0, len(recs), 8):
        rs = recs[i:i + 8]
        for r, (t, _) in zip(rs, transcribe(model, proc, [load_wav(r["wav"]) for r in rs])):
            ref = lx.tokenize(r["text"]); err += edit_distance(lx.tokenize(t), ref); n += len(ref)
    return round(err / max(1, n), 3)


def label(a):
    from .data import G2P, load_wav
    model, proc = load_teacher(a.base)
    g2p = G2P()
    wavs = sorted(Path(a.audio).rglob("*.wav")) + sorted(Path(a.audio).rglob("*.flac"))
    kept = 0
    with Path(a.out).open("w") as f:
        for i in range(0, len(wavs), 8):
            ws = wavs[i:i + 8]
            for w, (t, c) in zip(ws, transcribe(model, proc, [load_wav(str(x)) for x in ws])):
                ok = c >= a.min_conf and t and g2p.targets(t) is not None
                if ok:
                    f.write(json.dumps({"wav": str(w), "text": " ".join(lx.tokenize(t)), "teacher_conf": round(c, 3),
                                        "source": "whisper-lora"}) + "\n")
                    kept += 1
    print(f"labelled {kept}/{len(wavs)} clips (confidence >= {a.min_conf}) -> {a.out}")


def score(a):
    model, proc = load_teacher(a.base)
    for s in a.set:
        print(s, "WER", score_recs(model, proc, _recs(s)))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fasr whisper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--train", action="append", required=True)
    t.add_argument("--dev", required=True)
    t.add_argument("--hours", type=float, default=2)
    t.add_argument("--bs", type=int, default=8)
    t.add_argument("--lr", type=float, default=1e-4)
    l = sub.add_parser("label")
    l.add_argument("--audio", required=True)
    l.add_argument("--out", required=True)
    l.add_argument("--min-conf", type=float, default=0.80)
    s = sub.add_parser("score")
    s.add_argument("--set", action="append", required=True)
    for p in (t, l, s):
        p.add_argument("--base", default="openai/whisper-small")
    a = ap.parse_args(argv)
    {"train": train, "label": label, "score": score}[a.cmd](a)
