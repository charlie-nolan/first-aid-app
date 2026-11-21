"""The device's own log-mel front end (firmware/components/asr/fasr_frontend.c), run on the PC.

Charlie OK'd E1 on 2026-11-20: the S3 computes features with a float32 FFT (FE_FAST, ~20x faster than
float64 there), which moves features ~1 ulp from numpy. So the PC builds the exact same C file with the
exact same float flags and uses it for golden files and evaluation; the numpy version (features.logmel_np)
stays as the readable reference. Source copied from claude/project-thread-lvj6uv at 045d6d8 into csrc/.

Build: cc -O2 -fPIC -shared -DFE_FAST -ffp-contract=off  (no -ffast-math: keeps the chip's rounding).
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import subprocess
from pathlib import Path

import numpy as np

SRC = Path(__file__).parent / "csrc" / "fasr_frontend.c"
FLAGS = ["-O2", "-fPIC", "-shared", "-DFE_FAST", "-ffp-contract=off", "-fno-fast-math"]
NAME = "fe_float32 (fasr_frontend.c FE_FAST, -ffp-contract=off)"
_lib = None


def _build() -> Path:
    h = hashlib.sha1(SRC.read_bytes() + " ".join(FLAGS).encode()).hexdigest()[:12]
    cache = Path(os.environ.get("FASR_CACHE", Path.home() / ".cache" / "fasr"))
    so = cache / f"fasr_fe_{h}.so"
    if not so.exists():
        cache.mkdir(parents=True, exist_ok=True)
        tmp = so.with_suffix(f".{os.getpid()}.tmp")
        subprocess.run([os.environ.get("CC", "cc"), *FLAGS, "-I", str(SRC.parent), str(SRC), "-o", str(tmp), "-lm"], check=True)
        tmp.replace(so)
    return so


def lib():
    global _lib
    if _lib is None:
        L = ctypes.CDLL(str(_build()))
        L.fe_frames.argtypes, L.fe_frames.restype = [ctypes.c_int], ctypes.c_int
        L.fe_logmel.argtypes = [ctypes.POINTER(ctypes.c_int16), ctypes.c_int, ctypes.POINTER(ctypes.c_float)]
        L.fe_logmel.restype = ctypes.c_int
        _lib = L
    return _lib


def to_int16(x: np.ndarray) -> np.ndarray:
    """float [-1,1] -> int16 the way the mic delivers it (the C reads it back as v/32768)."""
    x = np.asarray(x)
    if x.dtype == np.int16:
        return x
    return np.clip(np.round(x.astype(np.float64) * 32768.0), -32768, 32767).astype(np.int16)


def logmel(x: np.ndarray) -> np.ndarray:
    pcm = np.ascontiguousarray(to_int16(x))
    L = lib()
    out = np.empty((L.fe_frames(len(pcm)), 40), np.float32)
    L.fe_logmel(pcm.ctypes.data_as(ctypes.POINTER(ctypes.c_int16)), len(pcm), out.ctypes.data_as(ctypes.POINTER(ctypes.c_float)))
    return out
