"""Speak first-aid sentences with many offline Piper voices: the recognizer's main training data.

    python -m fasr synth --trees ~/first-aid-build/pipeline/plb/content/trees \
        --extra ../voice_router/out/generated --out $WORK/speech/tts [--per-text 8] [--cuda]

Sentences: tree phrases/labels, voice-router `train` seeds, generated sentences (--extra),
plus template sentences built from the lexicon classes ("my left ankle is swollen").
HELD-OUT sentences (voice `test`, phrases/synthetic) are never spoken into the training set.

Voices: speakers are split by a fixed rule so the dev and eval sets are people the model
never trained on: whole voices in HOLDOUT_VOICES, and every 10th speaker of multi-speaker
voices. Train clips are saved CLEAN; noise is added on the fly while training.
eval.jsonl = held-out sentences x held-out speakers x 5 noise conditions (baked in),
the same set used to compare against MultiNet on the bench.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import wave
from pathlib import Path

import numpy as np
import yaml

from . import lexicon as lx

GPU_DIR = Path(__file__).resolve().parents[2] / "gpu"
HOLDOUT_VOICES = {"en_US-ryan-medium", "en_GB-alan-medium", "en_US-kristin-medium"}
CONDITIONS = [("clean", None, None), ("wind", "wind", 15), ("wind", "wind", 5),
              ("water", "water", 10), ("breath", "breath", 12)]

TEMPLATES = [
    "my {b} {v}", "my {b} is {s}", "his {b} is {s}", "her {b} is {s}", "i hurt my {b}",
    "i {m} my {b}", "he {m} his {b}", "she {m} her {b}", "i can't move my {b}", "i can't feel my {b}",
    "my {side} {b} {v}", "{b} is {s}", "there's something wrong with my {b}", "pain in my {b}",
    "he got {m2} by a {a}", "she was {m2} by a {a}", "a {a} {m3} me", "it's {s} and {s2}",
]
BODY_EXTRA = ["arm", "leg", "ankle", "knee", "wrist", "hand", "foot", "head", "back", "neck", "shoulder", "hip", "finger"]
VERBS = ["hurts", "is bleeding", "is swollen", "is numb", "looks wrong", "won't move", "is bent", "feels cold"]
SYMPTOMS = ["swollen", "bleeding", "numb", "broken", "bruised", "cold", "pale", "tingling", "bent", "burned"]
MECH = ["twisted", "broke", "cut", "burned", "sprained", "banged", "crushed", "hurt", "scraped", "jammed"]
MECH2 = ["bitten", "stung"]
MECH3 = ["bit", "stung"]
ANIMALS = ["snake", "dog", "bee", "wasp", "spider", "tick", "hornet", "rattlesnake", "scorpion"]


def template_sentences(n: int, rng: random.Random) -> list[str]:
    known = {r["word"] for r in lx.load_existing()} or None
    out = []
    for _ in range(n):
        t = rng.choice(TEMPLATES)
        s = t.format(b=rng.choice(BODY_EXTRA), v=rng.choice(VERBS), s=rng.choice(SYMPTOMS), s2=rng.choice(SYMPTOMS),
                     m=rng.choice(MECH), m2=rng.choice(MECH2), m3=rng.choice(MECH3), a=rng.choice(ANIMALS),
                     side=rng.choice(["left", "right"]))
        if known is None or all(w in known for w in lx.tokenize(s)):
            out.append(s)
    return out


def norm(s: str) -> str:
    return " ".join(lx.tokenize(s))


def sentences(trees, extra, n_templates, rng, style: str = "all") -> tuple[list[str], list[str]]:
    """style "all": tree text (card titles, steps) + router phrases + extra + templates.
    style "user": only things a person says TO the device (router/expert train phrases, the router
    thread's Qwen sentences in extra, templates): card titles read like the manual, not like a request."""
    held = {norm(s) for s in lx.held_out_texts()}
    pool = (lx.tree_texts(trees) if style == "all" else []) + lx._voice_split("train") + lx.extra_texts(extra)
    pool += template_sentences(n_templates, rng)
    # numbers and navigation words on their own, many times (they decide the tile screens)
    pool += ["one", "two", "three", "four", "number one", "number two", "number three", "number four",
             "option one", "option two", "option three", "option four", "yes", "no", "back", "go back",
             "again", "say that again", "home", "go home", "start over"] * 6
    seen, train = set(), []
    for s in pool:
        k = norm(s)
        if k and k not in held and (k not in seen or len(k.split()) <= 2):
            seen.add(k)
            train.append(k)
    return train, sorted(held)


def speaker_split(name: str, spk: int | None) -> str:
    if name in HOLDOUT_VOICES:
        return "held"
    if spk is not None and spk % 10 == 0:
        return "held"
    return "train"


def write_wav(path: Path, x: np.ndarray, sr: int):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
        wf.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fasr synth")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--voices", type=Path, help="default $WORK/voices (gpu/setup.sh downloads them)")
    ap.add_argument("--trees", nargs="*", default=[], type=Path)
    ap.add_argument("--extra", nargs="*", default=[], type=Path)
    ap.add_argument("--per-text", type=int, default=8)
    ap.add_argument("--templates", type=int, default=3000)
    ap.add_argument("--eval-per-text", type=int, default=3)
    ap.add_argument("--cuda", action="store_true")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--style", choices=["all", "user"], default="all",
                    help="user = only request-style sentences (no card titles)")
    ap.add_argument("--workers", type=int, default=1, help="CPU processes, each speaking every Nth clip")
    ap.add_argument("--threads", type=int, default=2, help="onnxruntime threads per CPU process")
    ap.add_argument("--shard", default="0/1", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.workers > 1 and a.shard == "0/1":
        return run_workers(a, list(argv if argv is not None else sys.argv[2:]))
    k, nsh = map(int, a.shard.split("/"))
    sys.path.insert(0, str(GPU_DIR))
    from make_clips import SR, load_voices, mix, speak

    rng = random.Random(a.seed + 7919 * k); nrng = np.random.default_rng(a.seed + k)
    vdir = a.voices or (a.out.parent.parent / "voices")
    voices = load_voices(vdir, a.cuda, 0 if nsh == 1 else a.threads)
    train_s, held_s = sentences(a.trees or [lx.CONTENT / "trees"], a.extra, a.templates, random.Random(a.seed), a.style)
    print(f"{len(train_s)} training sentences, {len(held_s)} held-out sentences, {len(voices)} voices", flush=True)

    def pick(split):
        for _ in range(200):
            name, v = rng.choice(voices)
            n = getattr(v.config, "num_speakers", 1)
            spk = rng.randrange(n) if n > 1 else None
            if speaker_split(name, spk) == split:
                return name, v, spk
        raise SystemExit(f"no voices for split {split}")

    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "wav").mkdir(exist_ok=True)
    done = set()
    for p in a.out.glob("*.jsonl"):   # resume: keep what's there (merged manifests and unmerged shards)
        done |= {json.loads(l)["wav"] for l in p.read_text().splitlines() if l.strip()}
    tag = "" if nsh == 1 else f".s{k}"
    man = {sp: (a.out / f"{sp}{tag}.jsonl").open("a") for sp in ("train", "dev", "eval")}
    n = skipped = 0
    jobs = [("train", i, s, j) for i, s in enumerate(train_s) for j in range(a.per_text)]
    jobs += [("dev", i, s, 0) for i, s in enumerate(train_s) if i % 20 == 0]          # unseen speakers, seen text
    jobs += [("eval", i, s, j) for i, s in enumerate(held_s) for j in range(a.eval_per_text * len(CONDITIONS))]
    for idx, (split, i, s, j) in enumerate(jobs):
        rel = f"wav/{split}-{i:05d}-{j:02d}.wav"
        if idx % nsh != k or rel in done:
            continue
        name, v, spk = pick("train" if split == "train" else "held")
        speed = rng.uniform(0.8, 1.25)
        try:
            x = speak(v, s, spk, speed)
        except Exception as e:
            print("skip", name, s, e); skipped += 1
            if skipped > 50 and skipped > n:   # a broken voice setup, not a bad sentence: don't mark synth done
                raise SystemExit(f"synth: {skipped} failures vs {n} clips, stopping")
            continue
        rec = {"wav": rel, "text": s, "voice": name, "speaker": spk, "speed": round(speed, 2)}
        if split == "eval":
            label, kind, snr = CONDITIONS[j % len(CONDITIONS)]
            x = mix(x, kind, snr, nrng)
            rec.update(noise=label, snr=snr)
        write_wav(a.out / rel, x, SR)
        rec["secs"] = round(len(x) / SR, 2)
        man[split].write(json.dumps(rec) + "\n")
        n += 1
        if n % 500 == 0:
            for m in man.values():
                m.flush()
            print(f"{n} clips", flush=True)
    for m in man.values():
        m.close()
    print(f"done: {n} new clips in {a.out} ({skipped} skipped)", flush=True)


def run_workers(a, argv: list[str]):
    """Start N shard processes, wait, then fold their manifests into train/dev/eval.jsonl."""
    import subprocess
    here = Path(__file__).resolve().parents[1]
    procs = [subprocess.Popen([sys.executable, "-m", "fasr", "synth", *argv, "--shard", f"{k}/{a.workers}"], cwd=here)
             for k in range(a.workers)]
    bad = [k for k, p in enumerate(procs) if p.wait() != 0]
    for sp in ("train", "dev", "eval"):
        with (a.out / f"{sp}.jsonl").open("a") as out:
            for f in sorted(a.out.glob(f"{sp}.s*.jsonl")):
                out.write(f.read_text()); f.unlink()
    counts = {sp: sum(1 for _ in (a.out / f"{sp}.jsonl").open()) for sp in ("train", "dev", "eval")}
    print("synth clips:", counts, flush=True)
    if bad:
        raise SystemExit(f"synth shards {bad} failed (finished clips are kept; rerun to resume)")
