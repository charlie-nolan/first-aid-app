"""Log-mel features, computed the same way here and on the ESP32-S3.

16 kHz mono, 25 ms Hann window (400 samples), 10 ms hop (160), 512-point FFT,
40 mel bands 60-7600 Hz (the PDM mic gives little below/above), log(power + 1e-6).
Per-utterance mean removal is NOT used (it needs the whole clip; the device streams);
instead the model starts with a fixed affine norm learned from training data stats.
The numpy version (logmel_np) is the readable reference. Since E1 (2026-11-20) the device runs a
float32 FFT, so logmel() runs that same C file (fasr/cfe.py) for evaluation and golden files.
FASR_FE=numpy switches back to the float64 numpy path.
"""
from __future__ import annotations

import os

import numpy as np

SR, WIN, HOP, NFFT, NMEL, FMIN, FMAX = 16000, 400, 160, 512, 40, 60.0, 7600.0


def _hz2mel(f):
    return 2595.0 * np.log10(1.0 + f / 700.0)


def _mel2hz(m):
    return 700.0 * (10 ** (m / 2595.0) - 1.0)


def mel_matrix() -> np.ndarray:
    pts = _mel2hz(np.linspace(_hz2mel(FMIN), _hz2mel(FMAX), NMEL + 2))
    bins = np.fft.rfftfreq(NFFT, 1.0 / SR)
    m = np.zeros((NMEL, len(bins)), np.float32)
    for i in range(NMEL):
        lo, c, hi = pts[i], pts[i + 1], pts[i + 2]
        m[i] = np.clip(np.minimum((bins - lo) / (c - lo), (hi - bins) / (hi - c)), 0, None)
    return m


_MEL = mel_matrix()
_WIN = np.hanning(WIN + 1)[:WIN].astype(np.float32)


def frontend_name() -> str:
    if os.environ.get("FASR_FE", "device") == "numpy":
        return "numpy float64 (features.logmel_np)"
    from .cfe import NAME
    return NAME


def logmel(x: np.ndarray) -> np.ndarray:
    """int16 or float [-1,1] mono 16 kHz -> (frames, 40) float32, as the device computes it."""
    if os.environ.get("FASR_FE", "device") == "numpy":
        return logmel_np(x)
    from .cfe import logmel as c_logmel
    return c_logmel(x)


def logmel_np(x: np.ndarray) -> np.ndarray:
    """int16 or float [-1,1] mono 16 kHz -> (frames, 40) float32. float64 numpy reference."""
    x = np.asarray(x)
    if x.dtype == np.int16:
        x = x.astype(np.float32) / 32768.0
    x = x.astype(np.float32)
    if len(x) < WIN:
        x = np.pad(x, (0, WIN - len(x)))
    n = 1 + (len(x) - WIN) // HOP
    idx = np.arange(WIN)[None, :] + HOP * np.arange(n)[:, None]
    spec = np.abs(np.fft.rfft(x[idx] * _WIN, NFFT)) ** 2
    return np.log(spec @ _MEL.T + 1e-6).astype(np.float32)


def logmel_torch(x, mel=None):
    """Batched GPU version for training. x: (B, samples) float. Matches logmel()."""
    import torch
    if mel is None:
        mel = torch.from_numpy(_MEL).to(x.device)
    win = torch.from_numpy(_WIN).to(x.device)
    if x.shape[-1] < WIN:
        x = torch.nn.functional.pad(x, (0, WIN - x.shape[-1]))
    fr = x.unfold(-1, WIN, HOP) * win
    spec = torch.fft.rfft(fr, NFFT).abs() ** 2
    return torch.log(spec @ mel.T + 1e-6)
