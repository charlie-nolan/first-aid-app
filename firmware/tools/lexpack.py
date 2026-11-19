"""Speech lexicon -> LEXI chunk (word id -> word) for the board-only build.

    python lexpack.py pipeline/plb/content/lexicon.yaml -o lexi.bin     # from the speech thread's commit

Layout: "LEX1" u16 version(1) u16 reserved u32 n, n x u32 offset (0 = no word), NUL-terminated words.
Ids are the recognizer's (0 = <unk>); the form engine reads the words back as text.
"""
import argparse
import struct
from pathlib import Path

import yaml


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("lexicon", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True)
    a = ap.parse_args(argv)
    rows = yaml.safe_load(a.lexicon.read_text())["words"]
    n = max(r["id"] for r in rows) + 1
    words = {r["id"]: str(r["word"]) for r in rows}
    head = b"LEX1" + struct.pack("<HHI", 1, 0, n)
    offs, data = [], bytearray()
    base = len(head) + 4 * n
    for i in range(n):
        if i in words and i:
            offs.append(base + len(data)); data += words[i].encode() + b"\0"
        else:
            offs.append(0)
    blob = head + struct.pack(f"<{n}I", *offs) + bytes(data)
    a.out.write_bytes(blob)
    print(f"{a.out}: {n} ids, {len(words)} words, {len(blob)} bytes")


if __name__ == "__main__":
    main()
