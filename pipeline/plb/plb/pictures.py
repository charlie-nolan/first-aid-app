"""1-bit picture pipeline: crop -> trim -> scale -> threshold/dither -> 1-bit PNG.

Screen is 250x122. A figure that would shrink below `min_scale` to fit one screen is
kept larger as a "pan" image (up to pan_w x pan_h) and the firmware scrolls it.

Compression (RLE/G4) is the packer's job; this module hands it 1-bit PNGs.
"""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageOps

SCREEN_W, SCREEN_H = 250, 122


@dataclass
class Picture:
    w: int
    h: int
    pan: bool


def to_1bit(img: Image.Image, screen=(SCREEN_W, SCREEN_H), min_scale: float = 0.5,
            pan_max=(750, 366), mode: str = "auto", px_per_pt: float | None = None
            ) -> tuple[Image.Image, bool]:
    """px_per_pt: how many source pixels per PDF point (render dpi / 72). Pan decisions are
    made in printed size, so a big fold-out diagram pans while a small one fits, whatever
    dpi it was rendered at. Without it, decisions use source pixels."""
    g = ImageOps.autocontrast(img.convert("L"), cutoff=1)
    # trim white border
    inv = ImageOps.invert(g)
    box = inv.point(lambda v: 255 if v > 40 else 0).getbbox()
    if box:
        g = g.crop(box)
    w, h = g.size
    k = px_per_pt or 1.0                      # source px per printed point
    s = min(screen[0] / w, screen[1] / h, 1.0)
    pan = min(screen[0] / (w / k), screen[1] / (h / k)) < min_scale
    if pan:                                   # ~0.9 screen px per printed point
        s = min(0.9 / k, pan_max[0] / w, pan_max[1] / h)
    g = g.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)
    if mode == "auto":
        hist = g.histogram()
        mid = sum(hist[64:192]) / max(1, g.size[0] * g.size[1])
        mode = "dither" if mid > 0.25 else "threshold"
    if mode == "dither":
        bw = g.convert("1")  # Floyd-Steinberg
    else:
        t = _otsu(g)
        bw = g.point(lambda v: 255 if v > t else 0).convert("1", dither=Image.Dither.NONE)
    return bw, pan


def _otsu(g: Image.Image) -> int:
    hist = g.histogram()
    total = sum(hist)
    sum_all = sum(i * c for i, c in enumerate(hist))
    wb = sb = 0
    best, thr = -1.0, 128
    for t in range(256):
        wb += hist[t]
        if wb == 0:
            continue
        wf = total - wb
        if wf == 0:
            break
        sb += t * hist[t]
        mb, mf = sb / wb, (sum_all - sb) / wf
        between = wb * wf * (mb - mf) ** 2
        if between > best:
            best, thr = between, t
    return thr


def build_picture(src: str, dst: str, **kw) -> Picture:
    """kw: see to_1bit (pass px_per_pt for PDF figures)."""
    bw, pan = to_1bit(Image.open(src), **kw)
    bw.save(dst, optimize=True)
    return Picture(bw.size[0], bw.size[1], pan)
