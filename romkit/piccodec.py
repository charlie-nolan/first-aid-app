"""1-bit picture codecs for cartridge ROMs.

codec 0  raw rows, MSB first, 1 = black, rows padded to whole bytes
codec 1  PackBits on the raw rows (cheap fallback)
codec 2  JBIG-style context model: each pixel is coded with an adaptive binary
         range coder (LZMA's bit coder) using its 10 already-decoded neighbours
         as context. 1024 x u16 probabilities = 2 KB RAM while decoding.
"""
import numpy as np
from PIL import Image

RAW, PACKBITS, CTX = 0, 1, 2


def to_bits(img):
    """PIL image -> bool array (True = black)."""
    if img.mode != '1':
        img = img.convert('L').point(lambda v: 255 if v >= 128 else 0).convert('1')
    return ~np.array(img, dtype=bool)


def raw_rows(a):
    return np.packbits(a.astype(np.uint8), axis=1).tobytes()


def packbits(data):
    out, i, n = bytearray(), 0, len(data)
    while i < n:
        j = i
        while j + 1 < n and data[j + 1] == data[i] and j - i < 127: j += 1
        if j > i:
            out += bytes([257 - (j - i + 1), data[i]]); i = j + 1   # run of j-i+1
        else:
            k = i
            while k < n and k - i < 128 and not (k + 2 < n and data[k] == data[k + 1] == data[k + 2]): k += 1
            out.append(k - i - 1); out += data[i:k]; i = k
    return bytes(out)


class RangeEnc:  # LZMA rc encoder
    def __init__(self): self.low = 0; self.range = 0xFFFFFFFF; self.cache = 0; self.csize = 1; self.out = bytearray()
    def shift(self):
        if self.low < 0xFF000000 or self.low >= 1 << 32:
            carry = self.low >> 32; t = self.cache
            while True:
                self.out.append((t + carry) & 0xFF); t = 0xFF; self.csize -= 1
                if not self.csize: break
            self.cache = (self.low >> 24) & 0xFF
        self.csize += 1
        self.low = (self.low & 0x00FFFFFF) << 8
    def bit(self, probs, i, b):
        p = probs[i]; bound = (self.range >> 11) * p
        if b == 0: self.range = bound; probs[i] = p + ((2048 - p) >> 5)
        else: self.low += bound; self.range -= bound; probs[i] = p - (p >> 5)
        while self.range < 1 << 24: self.range = (self.range << 8) & 0xFFFFFFFF; self.shift()
    def finish(self):
        for _ in range(5): self.shift()
        return bytes(self.out)


def ctx_encode(a):
    h, w = a.shape
    pad = np.zeros((h + 2, w + 4), dtype=np.uint8); pad[2:, 2:-2] = a
    probs = [1024] * 1024
    rc = RangeEnc()
    for y in range(h):
        r2, r1, r0 = pad[y].tolist(), pad[y + 1].tolist(), pad[y + 2].tolist()
        for x in range(w):
            X = x + 2
            c = (r2[X-1] << 9 | r2[X] << 8 | r2[X+1] << 7 |
                 r1[X-2] << 6 | r1[X-1] << 5 | r1[X] << 4 | r1[X+1] << 3 | r1[X+2] << 2 |
                 r0[X-2] << 1 | r0[X-1])
            rc.bit(probs, c, int(r0[X]))
    return rc.finish()


def encode(a, codecs=(RAW, PACKBITS, CTX)):
    """Pick the smallest codec for this picture."""
    raw = raw_rows(a)
    best = (RAW, raw)
    if PACKBITS in codecs:
        pb = packbits(raw)
        if len(pb) < len(best[1]): best = (PACKBITS, pb)
    if CTX in codecs:
        cx = ctx_encode(a)
        if len(cx) < len(best[1]): best = (CTX, cx)
    return best


def encode_file(path):
    a = to_bits(Image.open(path))
    codec, data = encode(a)
    return a.shape[1], a.shape[0], codec, data
