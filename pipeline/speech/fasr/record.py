"""Record Charlie's voice: prompted sentences (labelled) and free talk (labelled later by Whisper).

    python -m fasr record --mode prompted --minutes 20      # read sentences off the screen
    python -m fasr record --mode eval --minutes 5           # HELD-OUT sentences, scoring only
    python -m fasr record --mode free --minutes 10          # describe made-up injuries freely

Press Enter to start a take, Enter to stop, `r` + Enter to redo the last one, `q` to quit.
Saved under $WORK/speech/me/ (16 kHz mono WAV + train.jsonl / eval.jsonl / free/).
Use the USB headset or laptop mic for now; once the Plus is on the bench, record through
the real PDM mic with bench_loop's capture so the model hears the device's own microphone.
Speak like you would on a trail: some calm, some rushed, some out of breath, outdoors too.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
import wave
from pathlib import Path

import numpy as np

from . import lexicon as lx

WORK = Path(os.environ.get("WORK", "/mnt/data/first-aid-gpu"))
ME = WORK / "speech" / "me"
STYLES = ["normal", "rushed", "quiet", "out of breath", "loud (wind)", "worried"]


def prompts(mode: str, rng: random.Random) -> list[str]:
    from .synth import sentences
    tr, held = sentences([lx.CONTENT / "trees"], [], 400, rng)
    s = held if mode == "eval" else tr
    rng.shuffle(s)
    return s


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fasr record")
    ap.add_argument("--mode", choices=["prompted", "eval", "free"], default="prompted")
    ap.add_argument("--minutes", type=float, default=20)
    ap.add_argument("--device", default=None, help="sounddevice input name/index")
    a = ap.parse_args(argv)
    import sounddevice as sd
    rng = random.Random(time.time())
    sub = "free" if a.mode == "free" else "wav"
    (ME / sub).mkdir(parents=True, exist_ok=True)
    man = ME / ("eval.jsonl" if a.mode == "eval" else "train.jsonl")
    ps = prompts(a.mode, rng) if a.mode != "free" else []
    t_end = time.time() + 60 * a.minutes
    i, last = 0, None
    print(__doc__.split("\n\n")[2])
    while time.time() < t_end:
        if a.mode == "free":
            text = None
            print("\nSay anything you might say after an injury (one situation per take).")
        else:
            text = ps[i % len(ps)]
            print(f"\n[{rng.choice(STYLES)}]  >>  {text}")
        cmd = input("Enter = record (r = redo last, q = quit): ").strip().lower()
        if cmd == "q":
            break
        if cmd == "r" and last:
            text, path = last
        else:
            path = ME / sub / f"{a.mode}-{int(time.time()*1000)}.wav"
        chunks = []
        st = sd.InputStream(samplerate=16000, channels=1, dtype="int16", device=a.device,
                            callback=lambda d, *_: chunks.append(d.copy()))
        with st:
            input("...recording, Enter to stop ")
        x = np.concatenate(chunks)[:, 0] if chunks else np.zeros(0, np.int16)
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000); wf.writeframes(x.tobytes())
        if text is not None and cmd != "r":
            with man.open("a") as f:
                f.write(json.dumps({"wav": str(path), "text": text, "speaker": "charlie",
                                    "secs": round(len(x) / 16000, 2)}) + "\n")
        last = (text, path)
        i += 1
    print(f"saved {i} takes in {ME}")
