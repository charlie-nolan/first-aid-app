"""Make spoken test clips from the voice stress-test sentences, fully offline.

Each held-out utterance in pipeline/plb/content/phrases/synthetic/*.yaml (and every
registered phrase, as a control) is spoken by several Piper voices and speakers at
different speeds, then mixed with wind, rushing-water and breath noise at a few
loudness levels. Output: 16 kHz mono WAVs plus manifest.jsonl, ready for
bench_loop.py (real ESP-SR on the Plus) and ranker_eval.py.

    python make_clips.py --out $WORK/clips [--per-text 6] [--cuda]
    python make_clips.py --keys --out $WORK/keyclips     # numbered-tile words only
"""
from __future__ import annotations

import argparse, json, random, sys, wave
from pathlib import Path

import numpy as np
from scipy.signal import lfilter, resample_poly

HERE = Path(__file__).resolve().parent
PLB = HERE.parent / "plb"
sys.path.insert(0, str(PLB))
from plb.voicetest import load_utterances  # noqa: E402

SR = 16000

# Numbered tiles (firmware LIB_VOICE_NUMBERS): what people will say inside a topic,
# including the sound-alikes that should land on the same tile, and chatter that
# should be ignored or asked again. want = the key bench_loop.py expects.
KEY_UTTERANCES = (
    [(w, "key:1") for w in ["one", "number one", "option one", "one please", "the first one"]]
    + [(w, "key:2") for w in ["two", "number two", "option two", "to", "too"]]
    + [(w, "key:3") for w in ["three", "number three", "option three", "tree"]]
    + [(w, "key:4") for w in ["four", "number four", "option four", "for", "fore"]]
    + [(w, "key:back") for w in ["back", "go back"]]
    + [(w, "key:again") for w in ["again", "read again", "repeat", "say that again"]]
    + [(w, "key:home") for w in ["home", "go home", "start over"]]
)
KEY_OFF_TOPIC = ["okay", "hold on", "what", "um", "wait a second", "yeah", "hey", "let me see",
                 "are you okay", "stay with me", "ten", "won't", "free", "number"]
CONDITIONS = [("clean", None, None), ("wind", "wind", 15), ("wind", "wind", 5),
              ("water", "water", 10), ("breath", "breath", 12)]


def load_voices(vdir: Path, cuda: bool):
    from piper import PiperVoice
    out = []
    for onnx in sorted(vdir.glob("*.onnx")):
        try:
            v = PiperVoice.load(str(onnx), use_cuda=cuda)
        except TypeError:
            v = PiperVoice.load(str(onnx))
        out.append((onnx.stem, v))
    if not out:
        sys.exit(f"no voices in {vdir}: run setup.sh first")
    return out


def speak(voice, text: str, speaker: int | None, speed: float) -> np.ndarray:
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        try:  # piper-tts >= 1.3
            from piper import SynthesisConfig
            cfg = SynthesisConfig(speaker_id=speaker, length_scale=speed, noise_scale=0.667, noise_w_scale=0.8)
            voice.synthesize_wav(text, wf, syn_config=cfg)
        except ImportError:  # older piper-tts
            voice.synthesize(text, wf, speaker_id=speaker, length_scale=speed)
    buf.seek(0)
    with wave.open(buf, "rb") as rf:
        sr, raw = rf.getframerate(), rf.readframes(rf.getnframes())
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    g = np.gcd(SR, sr)
    return resample_poly(x, SR // g, sr // g).astype(np.float32)


def noise(kind: str, n: int, rng: np.random.Generator) -> np.ndarray:
    w = rng.standard_normal(n).astype(np.float32)
    if kind == "wind":       # brown noise with slow gusts
        x = lfilter([1], [1, -0.995], w)
        gust = np.interp(np.arange(n), np.linspace(0, n, 8), rng.uniform(0.2, 1.0, 8))
        x *= gust
    elif kind == "water":    # pink-ish noise (rushing stream, rain)
        x = lfilter([0.049922, -0.095993, 0.050613, -0.004408], [1, -2.494956, 2.017265, -0.522189], w)
    else:                    # breath: band-limited bursts
        x = lfilter([1, -1], [1, -0.7], w)
        env = np.zeros(n, np.float32)
        for start in rng.integers(0, max(1, n - SR // 2), 3):
            env[start:start + SR // 3] = np.hanning(len(env[start:start + SR // 3]))
        x *= env
    return (x / (np.sqrt(np.mean(x ** 2)) + 1e-9)).astype(np.float32)


def mix(speech: np.ndarray, kind: str | None, snr: float | None, rng) -> np.ndarray:
    pad = np.zeros(int(SR * 0.4), np.float32)
    x = np.concatenate([pad, speech, pad])
    if kind:
        p = np.sqrt(np.mean(speech ** 2)) + 1e-9
        x = x + noise(kind, len(x), rng) * p / (10 ** (snr / 20))
    x *= rng.uniform(0.3, 0.9) / (np.max(np.abs(x)) + 1e-9)   # different mic distances
    return x


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--voices", type=Path, default=None, help="default $WORK/voices next to --out")
    ap.add_argument("--per-text", type=int, default=6, help="voices/speakers per sentence")
    ap.add_argument("--conditions", default="all", help="'all' or a comma list: clean,wind,water,breath")
    ap.add_argument("--include-phrases", action="store_true", default=True)
    ap.add_argument("--cuda", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--keys", action="store_true", help="only the numbered-tile words (one..four, back, again, home)")
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed); random.seed(a.seed)
    vdir = a.voices or a.out.parent / "voices"
    voices = load_voices(vdir, a.cuda)

    if a.keys:
        utts = [{"text": t, "want": [k], "topic": "keys", "file": "keys", "off_topic": False} for t, k in KEY_UTTERANCES]
        utts += [{"text": t, "want": [], "topic": "keys", "file": "keys-off", "off_topic": True} for t in KEY_OFF_TOPIC]
    else:
        utts = load_utterances(PLB / "content" / "phrases" / "synthetic")
    if a.include_phrases and not a.keys:
        inter = PLB / "build" / "intermediate"
        trees = json.loads((inter / "trees.json").read_text())
        keys = {n["id"]: n["key"] for n in trees["nodes"]}
        for c in json.loads((inter / "phrases.json").read_text()):
            utts.append({"text": c["spoken"], "want": [keys[c["node"]]], "topic": keys[c["node"]].split("/")[0],
                         "file": "registered", "off_topic": False})
    conds = CONDITIONS if a.conditions == "all" else [c for c in CONDITIONS if c[0] in a.conditions.split(",")]

    a.out.mkdir(parents=True, exist_ok=True)
    man = (a.out / "manifest.jsonl").open("w")
    n = 0
    for i, u in enumerate(utts):
        for j in range(a.per_text):
            name, v = random.choice(voices)
            spk = random.randrange(v.config.num_speakers) if getattr(v.config, "num_speakers", 1) > 1 else None
            speed = random.uniform(0.85, 1.25)
            try:
                speech = speak(v, u["text"], spk, speed)
            except Exception as e:  # one bad voice shouldn't stop the run
                print("skip", name, u["text"], e); continue
            label, kind, snr = conds[(i + j) % len(conds)]
            x = mix(speech, kind, snr, rng)
            f = a.out / f"{i:04d}-{j}.wav"
            with wave.open(str(f), "wb") as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(SR)
                wf.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
            man.write(json.dumps({"wav": f.name, "text": u["text"], "want": u["want"], "off_topic": u["off_topic"],
                                  "source": u["file"], "voice": name, "speaker": spk, "speed": round(speed, 2),
                                  "noise": label, "snr": snr, "secs": round(len(x) / SR, 2)}) + "\n")
            n += 1
        if i % 25 == 0:
            print(f"{i}/{len(utts)} sentences, {n} clips", flush=True)
    print(f"done: {n} clips -> {a.out}")


if __name__ == "__main__":
    main()
