"""Copy clips from the recorder firmware (firmware/recorder) and make its prompt list.

    python -m fasr devrec prompts               # writes firmware/recorder/main/prompts.txt
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


def make_prompts(n_train: int, n_eval: int, seed: int = 5):
    from .synth import sentences
    rng = random.Random(seed)
    trees = [lx.CONTENT / "trees"]
    for d in (Path.home() / "first-aid-build/pipeline/plb/content/trees",):
        if d.exists():
            trees.append(d)
    tr, held = sentences(trees, [], 400, rng)
    tr = [s for s in tr if 2 <= len(s.split()) <= 14 and s.isascii()]
    singles = ["one", "two", "three", "four", "yes", "no", "back", "again", "home"]
    rng.shuffle(tr); rng.shuffle(held)
    train = singles + tr[: n_train - len(singles)]
    rng.shuffle(train)
    held = [s for s in held if s.isascii()][:n_eval]
    PROMPTS.write_text("".join(f"T|{s}\n" for s in train) + "".join(f"E|{s}\n" for s in held))
    print(f"{PROMPTS}: {len(train)} training + {len(held)} held-out prompts")


class Store:
    def __init__(self):
        OUT.mkdir(parents=True, exist_ok=True)
        self.rows: dict[str, dict] = {}
        for m in ("train", "eval", "free"):
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
        if hdr.get("redo_of", -1) >= 0:   # drop the clip this one replaces
            for k, r in list(self.rows.items()):
                if r.get("dev_id") == hdr["redo_of"] and r["mode"] == mode:
                    Path(r["wav"]).unlink(missing_ok=True); del self.rows[k]
        key = f"{mode}-{stamp}-{hdr['id']}"
        self.rows[key] = {"key": key, "wav": str(wav), "text": hdr.get("text", ""), "mode": mode, "dev_id": hdr["id"],
                          "prompt": hdr["prompt"], "speaker": "charlie", "mic": "pdm-" + hdr.get("board", "?"),
                          "peak": hdr["peak"], "clip_per_mille": hdr["clip_per_mille"],
                          "secs": round(hdr["samples"] / hdr["sr"], 2)}
        for m in ("train", "eval", "free"):
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
                flag = "  TOO LOUD" if hdr["clip_per_mille"] > 5 else "  quiet" if hdr["peak"] < 1000 else ""
                print(f"#{hdr['id']} {hdr['mode']:5s} {hdr['samples']/hdr['sr']:4.1f}s  {hdr.get('text','')[:60]}{flag}")
            else:
                print(f"#{hdr['id']}: bad transfer, asking again")
                s.write(b"RESEND\n")
            hdr = None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fasr devrec")
    ap.add_argument("what", nargs="?", default="run", choices=["run", "prompts"])
    ap.add_argument("--port")
    ap.add_argument("--train", type=int, default=400)
    ap.add_argument("--eval", type=int, default=120)
    a = ap.parse_args(argv)
    if a.what == "prompts":
        make_prompts(a.train, a.eval)
    else:
        try:
            run(a.port)
        except KeyboardInterrupt:
            print("stopped")
