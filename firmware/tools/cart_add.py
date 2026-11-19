"""Add (or replace) chunks in a cartridge (CRT1, romkit/FORMAT.md) for the board-only voice build.

    python cart_add.py library_personal.bin -o library_voice.bin FORM=form.fpk LEXI=lexicon.bin ASRM=model.bin

Readers find chunks by tag and ignore tags they don't know, so the cart still opens in every older
firmware. Chunks used by the standalone firmware:
  FORM  the form pack (components/vform/tools/formpack.py, FPK1)
  LEXI  the speech lexicon: word id -> word (tools/lexpack.py)
  ASRM  the int8 speech model (speech thread's export; not built yet)
  ASRC  the calibrated reject (reject_calib.json from the same speech run)
The header fields (home tree, sleep picture, counts, build id) are kept; offsets and the CRC are
rebuilt. A personal cart stays personal: never commit the output.
"""
from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path

HDR = 28


def read(blob: bytes) -> tuple[bytes, list[tuple[bytes, bytes]]]:
    if blob[:4] != b"CRT1":
        sys.exit("not a CRT1 cartridge")
    ver, n, total, _crc = struct.unpack_from("<HHII", blob, 4)
    chunks = []
    for i in range(n):
        tag, off, size = struct.unpack_from("<4sII", blob, HDR + 12 * i)
        chunks.append((tag, blob[off:off + size]))
    return blob[:HDR], chunks


def write(hdr: bytes, chunks: list[tuple[bytes, bytes]]) -> bytes:
    pos = HDR + 12 * len(chunks)
    dirent, body = b"", b""
    for tag, data in chunks:
        dirent += tag + struct.pack("<II", pos + len(body), len(data))
        body += data + b"\0" * (-len(data) % 4)
    blob = bytearray(hdr + dirent + body)
    struct.pack_into("<HI", blob, 6, len(chunks), len(blob))
    struct.pack_into("<I", blob, 12, zlib.crc32(bytes(blob[16:])))
    return bytes(blob)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cart", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("chunks", nargs="+", help="TAG=file (4-character tag)")
    a = ap.parse_args(argv)
    hdr, chunks = read(a.cart.read_bytes())
    for spec in a.chunks:
        tag, _, path = spec.partition("=")
        if len(tag) != 4 or not path:
            sys.exit(f"bad chunk {spec!r}: want TAG=file with a 4-character tag")
        data = Path(path).read_bytes()
        chunks = [(t, d) for t, d in chunks if t != tag.encode()] + [(tag.encode(), data)]
    out = write(hdr, chunks)
    a.out.write_bytes(out)
    print(f"{a.out}: {len(out)} bytes, chunks " + " ".join(f"{t.decode()}:{len(d)}" for t, d in chunks[-len(a.chunks):]))


if __name__ == "__main__":
    main()
