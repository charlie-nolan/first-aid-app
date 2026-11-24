"""Training data: manifests, text -> phone targets, and on-the-fly augmentation.

A manifest is JSON lines: {"wav": path (absolute, or relative to the manifest), "text": "..."}.
Text becomes tokens through lexicon.yaml first, then CMUdict (so general English from open
speech corpora works for pretraining). Utterances with a word neither knows are skipped.

Augmentation happens per batch on the fly, so every epoch hears new conditions:
speed 0.85-1.2, synthetic wind/water/breath (same generators as gpu/make_clips.py) and
optional real noise files, SNR 0-25 dB, small-room echo, a PDM-mic-like band-pass,
random gain and occasional clipping, then SpecAugment on the features.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import yaml
from scipy.signal import butter, fftconvolve, lfilter, resample_poly

from . import lexicon as lx
from .features import SR
from .phones import encode_words

GPU_DIR = Path(__file__).resolve().parents[2] / "gpu"
sys.path.insert(0, str(GPU_DIR))
try:
    from make_clips import noise as synth_noise  # noqa: E402
except Exception:  # make_clips needs piper only for speak(); noise() is plain numpy
    synth_noise = None


class G2P:
    """word -> first sound spelling; lexicon rows win over CMUdict."""

    def __init__(self, lexicon_path: Path = lx.LEXICON):
        self.map: dict[str, list[str]] = {}
        if Path(lexicon_path).exists():
            for r in yaml.safe_load(Path(lexicon_path).read_text())["words"]:
                if r["phones"]:
                    self.map[r["word"]] = r["phones"][0].split()

    def __call__(self, word: str) -> list[str] | None:
        p = self.map.get(word)
        if p is None:
            ps, how = lx.prons(word)
            p = ps[0] if ps else None
            if p is not None:
                self.map[word] = p
        return p

    def targets(self, text: str) -> list[int] | None:
        prons = []
        for w in lx.tokenize(text):
            p = self(w)
            if not p:
                return None
            prons.append(p)
        return encode_words(prons) if prons else None


def read_manifest(path: Path, g2p: G2P, max_secs: float = 16.0) -> list[dict]:
    path = Path(path)
    out, skipped = [], 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("secs", 0) > max_secs:
            continue
        wav = Path(r["wav"])
        if not wav.is_absolute():
            wav = path.parent / wav
        y = g2p.targets(r["text"])
        if y is None:
            skipped += 1
            continue
        out.append({"wav": str(wav), "text": r["text"], "y": y, "w": float(r.get("weight", 1.0))})
    print(f"{path}: {len(out)} utterances ({skipped} skipped: unknown words)", flush=True)
    return out


def load_wav(path: str) -> np.ndarray:
    x, sr = sf.read(path, dtype="float32", always_2d=True)
    x = x.mean(1)
    if sr != SR:
        g = np.gcd(SR, sr)
        x = resample_poly(x, SR // g, sr // g).astype(np.float32)
    return x


_BP = butter(2, [90 / (SR / 2), 7000 / (SR / 2)], "band")


class Augment:
    def __init__(self, noise_dir: Path | None = None, p_noise: float = 0.8, p_echo: float = 0.3, seed: int = 0,
                 mic_eq: Path | None = None, p_mic: float = 0.7):
        self.rng = np.random.default_rng(seed)
        # device-mic colouring from `fasr micmatch` (random strength, plus a random membrane-like high cut)
        self.mic = None
        if mic_eq and Path(mic_eq).exists():
            d = json.loads(Path(mic_eq).read_text())
            self.mic = (np.array(d["freq_hz"]), np.array(d["eq_db"]))
        self.p_mic = p_mic
        self.noise_files = sorted(Path(noise_dir).rglob("*.wav")) if noise_dir else []
        self.p_noise, self.p_echo = p_noise, p_echo
        self._cache: dict[str, np.ndarray] = {}

    def _noise(self, n: int) -> np.ndarray:
        r = self.rng
        if self.noise_files and r.random() < 0.5:
            f = str(self.noise_files[r.integers(len(self.noise_files))])
            if f not in self._cache and len(self._cache) < 200:
                self._cache[f] = load_wav(f)
            x = self._cache.get(f)
            if x is None:
                x = load_wav(f)
            if len(x) < n:
                x = np.tile(x, n // max(1, len(x)) + 1)
            s = r.integers(0, len(x) - n + 1)
            x = x[s:s + n]
            return x / (np.sqrt(np.mean(x ** 2)) + 1e-9)
        if synth_noise is not None:
            return synth_noise(["wind", "water", "breath"][r.integers(3)], n, r)
        return r.standard_normal(n).astype(np.float32)

    def _colour(self, x: np.ndarray) -> np.ndarray:
        r = self.rng
        f0, eq = self.mic
        X = np.fft.rfft(x)
        f = np.fft.rfftfreq(len(x), 1 / SR)
        db = r.uniform(0.4, 1.2) * np.interp(f, f0, eq)
        if r.random() < 0.4:                      # membrane / case: gentle high cut
            fc = r.uniform(2500, 6500)
            db -= 6 * np.log2(1 + (f / fc) ** 2) * r.uniform(0.5, 1.5)
        db += r.uniform(-3, 3) * (f / (SR / 2))   # random tilt
        return np.fft.irfft(X * 10 ** (db / 20), n=len(x)).astype(np.float32)

    def __call__(self, x: np.ndarray) -> np.ndarray:
        r = self.rng
        sp = r.uniform(0.85, 1.2)
        if abs(sp - 1) > 0.02:
            up = 100
            x = resample_poly(x, up, int(round(up * sp))).astype(np.float32)
        lead = np.zeros(int(SR * r.uniform(0.05, 0.4)), np.float32)
        tail = np.zeros(int(SR * r.uniform(0.05, 0.3)), np.float32)
        x = np.concatenate([lead, x, tail])
        if r.random() < self.p_echo:           # small room / tent / car: decaying noise tail
            n = int(SR * r.uniform(0.05, 0.3))
            rir = r.standard_normal(n).astype(np.float32) * np.exp(-np.arange(n) / (n / 5))
            rir[0] = 1.0
            x = fftconvolve(x, rir)[: len(x)].astype(np.float32)
        if r.random() < self.p_noise:
            p = np.sqrt(np.mean(x ** 2)) + 1e-9
            snr = r.uniform(0, 25)
            x = x + self._noise(len(x)) * p / (10 ** (snr / 20))
        if self.mic is not None and r.random() < self.p_mic:
            x = self._colour(x)
        x = lfilter(*_BP, x).astype(np.float32)
        x *= r.uniform(0.05, 0.95) / (np.max(np.abs(x)) + 1e-9)
        if r.random() < 0.05:
            x = np.clip(x * 3, -0.9, 0.9)
        return x.astype(np.float32)


def spec_augment(f: torch.Tensor, rng: random.Random, n_freq=2, F=6, n_time=2, T=20) -> torch.Tensor:
    f = f.clone()
    for _ in range(n_freq):
        w = rng.randint(0, F)
        s = rng.randint(0, max(0, f.shape[1] - w))
        f[:, s:s + w] = f.mean()
    for _ in range(n_time):
        w = rng.randint(0, min(T, max(1, f.shape[0] // 8)))
        s = rng.randint(0, max(0, f.shape[0] - w))
        f[s:s + w, :] = f.mean()
    return f


class SpeechSet(torch.utils.data.Dataset):
    def __init__(self, items: list[dict], augment: Augment | None):
        self.items, self.aug = items, augment

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        it = self.items[i]
        x = load_wav(it["wav"])
        if self.aug is not None:
            x = self.aug(x)
        return torch.from_numpy(x), torch.tensor(it["y"], dtype=torch.long), i


def collate(batch):
    xs, ys, ids = zip(*batch)
    xl = torch.tensor([len(x) for x in xs])
    yl = torch.tensor([len(y) for y in ys])
    X = torch.zeros(len(xs), int(xl.max()))
    for i, x in enumerate(xs):
        X[i, : len(x)] = x
    return X, xl, torch.cat(ys), yl, list(ids)


def weighted_sampler(items: list[dict], n: int | None = None):
    w = torch.tensor([it["w"] for it in items], dtype=torch.double)
    return torch.utils.data.WeightedRandomSampler(w, n or len(items), replacement=True)
