"""Copy clips from the recorder firmware (firmware/recorder) and make its prompt list.

    python -m fasr devrec prompts               # writes firmware/recorder/main/prompts.txt
    python -m fasr devrec extra                 # adds PAIRS + ANSWERS prompts, keeps TRAIN/EVAL lines
    python -m fasr devrec [--port /dev/ttyACM0] # leave running while you record

Clips land in $WORK/speech/me_dev/<mode>/ with manifests train.jsonl, eval.jsonl (prompted,
scoring only), free.jsonl (no text yet: Whisper labels them) and noise/ (background
recordings, used as real noise for augmentation). A redo replaces the earlier clip.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import random
import time
import wave
import zlib
from pathlib import Path

from . import lexicon as lx

WORK = Path(os.environ.get("WORK", "/mnt/data/first-aid-gpu"))
OUT = WORK / "speech" / "me_dev"
PROMPTS = Path(__file__).resolve().parents[3] / "firmware" / "recorder" / "main" / "prompts.txt"


GEN = Path(os.environ.get("GEN", Path.home() / "first-aid-voice/pipeline/voice_router/out/generated"))
AGY_EVAL = Path(__file__).resolve().parents[1] / "out" / "agy_eval.txt"


def _public_voice(which: str) -> list[str]:
    """`train` or `test` phrases from voice/topics.yaml and voice/experts/ only: never experts_personal/
    (Aerie-based, PC only), because prompts.txt is committed."""
    import yaml
    out = []
    vdir = lx.CONTENT / "voice"
    for f in [vdir / "topics.yaml", *sorted((vdir / "experts").glob("*.yaml"))]:
        if not f.exists():
            continue
        d = yaml.safe_load(f.read_text()) or {}
        groups = [d.get("reject", {})] + list((d.get("topics") or {}).values()) + list((d.get("nodes") or {}).values())
        for g in groups:
            out += [str(t) for t in (g or {}).get(which, [])]
    return out


def make_prompts(n_train: int, n_eval: int, seed: int = 5, new_only: bool = False):
    """Prompts in the words a person says TO the device ("my buddy cut his hand"), not card titles:
    TRAIN = router/expert `train` phrases + the router thread's Qwen sentences + short commands;
    EVAL  = held-out user-style sentences (voice `test` phrases, agy's independent set), never trained on."""
    rng = random.Random(seed)
    def ok(s): return 2 <= len(s.split()) <= 14 and s.isascii()
    seeds = [s.lower().strip() for s in _public_voice("train")]
    gen = [s.lower().strip() for s in lx.extra_texts([GEN])] if GEN.exists() else []
    held = [s.lower().strip() for s in _public_voice("test")]
    if AGY_EVAL.exists():
        held += [l.strip().lower() for l in AGY_EVAL.read_text().splitlines() if l.strip()]
    held_set = set(held)
    done = set()
    if new_only:   # a further recording round: skip sentences already recorded, keep the same EVAL list
        for m in (OUT / "train.jsonl",):
            if m.exists():
                done |= {json.loads(l)["text"].lower().strip() for l in m.read_text().splitlines() if l.strip()}
    pool = list(dict.fromkeys(s for s in seeds + gen if ok(s) and s not in held_set and s not in done))
    rng.shuffle(pool)
    singles = ["one", "two", "three", "four", "yes", "no", "back", "again", "home"]
    # every router seed first (hand-written), then Qwen sentences to fill
    seed_set = set(seeds)
    pool.sort(key=lambda s: s not in seed_set)
    train = singles + pool[: n_train - len(singles)]
    rng.shuffle(train)
    held = list(dict.fromkeys(s for s in held if ok(s)))
    rng.shuffle(held)
    held = held[:n_eval]
    if new_only and PROMPTS.exists():
        held = [l[2:] for l in PROMPTS.read_text().splitlines() if l.startswith("E|")] or held
    PROMPTS.write_text("".join(f"T|{s}\n" for s in train) + "".join(f"E|{s}\n" for s in held))
    print(f"{PROMPTS}: {len(train)} training + {len(held)} held-out prompts "
          f"({sum(s in seed_set for s in train)} router seeds, {len(gen)} Qwen sentences available)")


class Store:
    def __init__(self):
        OUT.mkdir(parents=True, exist_ok=True)
        self.rows: dict[str, dict] = {}
        for m in ("train", "eval", "free", "pairs", "answers"):
            f = OUT / f"{m}.jsonl"
            if f.exists():
                for l in f.read_text().splitlines():
                    if l.strip():
                        r = json.loads(l); self.rows[r["key"]] = r

    def add(self, hdr: dict, pcm: bytes):
        mode = hdr["mode"]
        d = OUT / mode; d.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        wav = d / f"{stamp}-{hdr['id']}.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(hdr["sr"]); w.writeframes(pcm)
        if mode == "noise":
            return wav
        if hdr.get("redo_of", -1) >= 0:   # drop the clip this one replaces: the NEWEST with that id
            # (ids restart if the board's saved state is erased, so older rows can share an id)
            same = [k for k, r in self.rows.items() if r.get("dev_id") == hdr["redo_of"] and r["mode"] == mode]
            if same:
                k = max(same)   # keys are "<mode>-<YYYYmmdd-HHMMSS>-<id>", so max = newest
                Path(self.rows[k]["wav"]).unlink(missing_ok=True); del self.rows[k]
        key = f"{mode}-{stamp}-{hdr['id']}"
        self.rows[key] = {"key": key, "wav": str(wav), "text": hdr.get("text", ""), "mode": mode, "dev_id": hdr["id"],
                          "prompt": hdr["prompt"], "speaker": "charlie", "mic": "pdm-" + hdr.get("board", "?"),
                          "peak": hdr["peak"], "clip_per_mille": hdr["clip_per_mille"],
                          "secs": round(hdr["samples"] / hdr["sr"], 2)}
        for m in ("train", "eval", "free", "pairs", "answers"):
            rows = [r for r in self.rows.values() if r["mode"] == m]
            (OUT / f"{m}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        return wav


def _find_port() -> str | None:
    from serial.tools import list_ports
    ps = [p.device for p in list_ports.comports() if "ACM" in p.device or "usbmodem" in p.device]
    return ps[0] if ps else None


def run(port: str | None):
    """Keeps going across unplug/replug and board resets: waits for the port, reconnects, resyncs."""
    import serial
    st = Store()
    seen: set[int] = set()
    waiting = False
    while True:
        p = port or _find_port()
        if not p:
            if not waiting:
                print("waiting for the board (plug in USB)...", flush=True); waiting = True
            time.sleep(1); continue
        waiting = False
        try:
            _session(serial.Serial(p, 115200, timeout=0.5), p, st, seen)
        except (serial.SerialException, OSError) as e:
            print(f"lost the board ({e.__class__.__name__}); reconnecting...", flush=True)
            time.sleep(1)


def _session(s, port: str, st: "Store", seen: set[int]):
    print(f"listening on {port}; clips -> {OUT}  (Ctrl-C to stop)", flush=True)
    s.write(b"HELLO\nRESEND\n")   # anything sent to an earlier, interrupted session comes again
    hdr, chunks, last_sync, n = None, [], 0.0, 0
    while True:
        if time.time() - last_sync > 1.5 and hdr is None:
            s.write(b"SYNC\n"); last_sync = time.time()
        raw = s.readline()
        if not raw:
            continue
        line = raw.decode("ascii", "replace").strip()
        if line.startswith("FASR-REC"):
            print("device:", line)
        elif line.startswith("SCREEN "):   # what the e-ink shows (lets you record with no panel wired)
            top, _, rest = line[7:].partition(" | ")
            prompt, _, status = rest.rpartition(" | ")
            print(f"\n[{top}]  {status}\n  >> {prompt}")
        elif line.startswith("TAKE "):
            hdr, chunks = json.loads(line[5:]), []
        elif line.startswith("D ") and hdr is not None:
            chunks.append(base64.b64decode(line[2:]))
        elif line.startswith("END ") and hdr is not None:
            pcm = b"".join(chunks)
            ok = len(pcm) == 2 * hdr["samples"] and format(zlib.crc32(pcm), "08x") == line[4:].strip()
            if ok and hdr["id"] in seen:
                s.write(f"ACK {hdr['id']}\n".encode())
            elif ok:
                seen.add(hdr["id"])
                wav = st.add(hdr, pcm)
                s.write(f"ACK {hdr['id']}\n".encode())
                n += 1
                import numpy as np   # judge the audio itself: a stuck DATA line reads as a loud constant "peak"
                x = np.frombuffer(pcm, np.int16).astype(np.float32)
                flag = ("  NO SIGNAL: the mic sends a flat line (check CLK/DATA/3V3/SEL wiring)" if x.size and (x.std() < 20 or np.unique(x, return_counts=True)[1].max() > x.size // 2)
                        else "  TOO LOUD" if hdr["clip_per_mille"] > 5 else "  quiet" if x.std() < 150 else "")
                print(f"#{hdr['id']} {hdr['mode']:5s} {hdr['samples']/hdr['sr']:4.1f}s  {hdr.get('text','')[:60]}{flag}")
            else:
                print(f"#{hdr['id']}: bad transfer, asking again")
                s.write(b"RESEND\n")
            hdr = None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fasr devrec")
    ap.add_argument("what", nargs="?", default="run", choices=["run", "prompts", "extra"])
    ap.add_argument("--port")
    ap.add_argument("--pages", help="extra: build ANSWERS from the router's intake_wordlists.yaml (PC only, don't commit)")
    ap.add_argument("--train", type=int, default=400)
    ap.add_argument("--eval", type=int, default=120)
    ap.add_argument("--new-only", action="store_true", help="skip sentences already recorded (next round); keep EVAL")
    ap.add_argument("--seed", type=int, default=5)
    a = ap.parse_args(argv)
    if a.what == "extra":
        from .extra_prompts import append
        append(PROMPTS, a.pages)
        return
    if a.what == "prompts":
        make_prompts(a.train, a.eval, seed=a.seed, new_only=a.new_only)
    else:
        try:
            run(a.port)
        except KeyboardInterrupt:
            print("stopped")
