"""Bundle bitmap fonts into the ROM's FONT chunk (CrossInk-style: fixed sizes,
pre-rendered from TTF at build time, picked at run time).

Rasterising follows CrossInk's fontconvert.py: FreeType renders each glyph
hinted and anti-aliased, then coverage at or above a low threshold becomes black
(this darkens thin stems instead of breaking them). Advances are FreeType's
unhinted linear advance in 1/16 px, and kerning comes from the font's GPOS/kern
tables (via HarfBuzz) in 1/16 px, so spacing never drifts.

FONT chunk:
  u8 n_faces, u8 n_sizes, u8 n_families, u8 pad
  u32 face_offset[n_faces]                  (from chunk start)
  char family_name[n_families][16]          (NUL padded)
  face i = ((family * n_sizes) + size) * 2 + bold
face (12-byte header):
  u8 px, u8 line_h, u8 ascent, u8 flags(1 = bold), u8 first_char, u8 n_glyphs,
  u16 n_kern, u16 space_adv16, u16 pad
  glyph[n_glyphs] = {u16 bitmap_off, u8 w, u8 h, i8 x_off, i8 y_off, u16 adv16}
  kern[n_kern]    = {u8 left_char, u8 right_char, i16 value16}  sorted by (left, right)
  bitmaps: w*h bits per glyph, row-major, MSB first, each starting on a byte
adv16 / value16 are 12.4 fixed point (1/16 px). Characters 32..126, and slot 127
is the bullet drawn for the \\x02 line marker.
"""
import os, struct
import numpy as np
import freetype

HERE = os.path.dirname(os.path.abspath(__file__))
F = lambda n: os.path.join(HERE, 'fonts', n)
DEFAULT = dict(
    families=[('Lexend', F('LexendDeca-Regular.ttf'), F('LexendDeca-Bold.ttf')),
              ('Bitter', F('Bitter-Regular.ttf'), F('Bitter-Bold.ttf'))],
    sizes=(10, 12, 15),          # pixel sizes: small, medium, large (Charlie, 2026-11-06)
    threshold=64,                # coverage 0-255 that counts as ink (picked from a 20-96 sweep)
    min_space_em=0.28,           # word gap never narrower than this
    autohint=False)
CHARS = [chr(c) for c in range(32, 127)] + ['•']
CODES = list(range(32, 128))


def kerning(path, px, chars):
    """{(a, b): value16} from GPOS/kern via HarfBuzz: shaped pair width minus the two advances."""
    try:
        import uharfbuzz as hb
    except ImportError:
        return {}
    blob = hb.Blob.from_file_path(path); face = hb.Face(blob); font = hb.Font(face)
    upem = face.upem
    def width(s):
        buf = hb.Buffer(); buf.add_str(s); buf.guess_segment_properties()
        hb.shape(font, buf, {'kern': True, 'liga': False, 'calt': False})
        return sum(p.x_advance for p in buf.glyph_positions)
    single = {c: width(c) for c in chars}
    out = {}
    letters = [c for c in chars if c.isalnum() or c in '.,;:\'"-()?!/']
    for a in letters:
        for b in letters:
            d = width(a + b) - single[a] - single[b]
            v = round(d * px * 16 / upem)
            if abs(v) >= 4: out[(a, b)] = v      # keep pairs of a quarter pixel or more
    return out


def face(path, px, bold, cfg):
    f = freetype.Face(path)
    f.set_pixel_sizes(0, px)
    flags = freetype.FT_LOAD_RENDER | freetype.FT_LOAD_NO_BITMAP
    if cfg['autohint']: flags |= freetype.FT_LOAD_FORCE_AUTOHINT
    recs, bits = [], bytearray()
    asc = (f.size.ascender + 63) >> 6
    desc = (-f.size.descender + 63) >> 6
    for ch in CHARS:
        gi = f.get_char_index(ord(ch))
        f.load_glyph(gi, flags)
        g = f.glyph; bm = g.bitmap
        adv16 = round(g.linearHoriAdvance / 65536 * 16)
        w, h = bm.width, bm.rows
        data = b''
        if w and h:
            a = np.array(bm.buffer, dtype=np.uint8).reshape(h, bm.pitch)[:, :w]
            ink = a >= cfg['threshold']
            data = np.packbits(ink.ravel()).tobytes()
        if ch == ' ':
            adv16 = max(adv16, round(cfg['min_space_em'] * px * 16))
        recs.append(struct.pack('<HBBbbH', len(bits), w, h, g.bitmap_left, -g.bitmap_top, adv16))
        bits += data
    kern = kerning(path, px, CHARS[:95])
    kern_b = b''.join(struct.pack('<BBh', ord(a), ord(b), v) for (a, b), v in sorted(kern.items()))
    line_h = asc + desc + max(1, px // 8)
    space16 = struct.unpack_from('<H', recs[0], 6)[0]
    head = struct.pack('<BBBBBBHHH', px, line_h, asc, 1 if bold else 0, 32, len(CHARS), len(kern), space16, 0)
    return head + b''.join(recs) + kern_b + bytes(bits)


def build(cfg=DEFAULT):
    faces = []
    for name, reg, bold in cfg['families']:
        for px in cfg['sizes']:
            faces.append(face(reg, px, False, cfg))
            faces.append(face(bold, px, True, cfg))
    names = b''.join(n.encode()[:15].ljust(16, b'\0') for n, _, _ in cfg['families'])
    off = 4 + 4 * len(faces) + len(names)
    table, body = b'', b''
    for fb in faces:
        while (off + len(body)) % 4: body += b'\0'
        table += struct.pack('<I', off + len(body)); body += fb
    return struct.pack('<BBBx', len(faces), len(cfg['sizes']), len(cfg['families'])) + table + names + body


if __name__ == '__main__':
    b = build(); print('FONT chunk', len(b), 'bytes')
