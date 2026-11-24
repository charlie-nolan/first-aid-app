"""Card keys -> cart node ids, as an NKEY chunk (tools/cart_add.py), so a voice result ("tree/card",
the form engine's node id) opens the real card in the library.

    python nkeypack.py <plb build>/intermediate_personal/trees.json -o nkey.bin

trees.json must come from the SAME bake as the cart. tools/nkey_check (C) verifies every entry
against the cart's own node labels before the cart is used.
Layout: "NK01" u32 n, n x {u32 key_off, u32 label_off, u16 node, u16 pad} sorted by key (bytes),
then NUL-terminated strings.
"""
import argparse, json, struct
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("trees_json", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--trees", nargs="*", help="only these trees (the voice trees: the slot files' names); default all")
    a = ap.parse_args(argv)
    nodes = json.loads(a.trees_json.read_text())["nodes"]
    if a.trees:
        keep = set(a.trees)
        nodes = [n for n in nodes if n["key"].split("/", 1)[0] in keep]
    rows = sorted(((n["key"], n["id"], n.get("label") or "") for n in nodes), key=lambda r: r[0].encode())
    head = b"NK01" + struct.pack("<I", len(rows))
    base = len(head) + 12 * len(rows)
    tab, strs = bytearray(), bytearray()
    for key, nid, label in rows:
        ko = base + len(strs); strs += key.encode() + b"\0"
        lo = base + len(strs); strs += label.encode() + b"\0"
        tab += struct.pack("<IIHH", ko, lo, nid, 0)
    a.out.write_bytes(head + bytes(tab) + bytes(strs))
    print(f"{a.out}: {len(rows)} card keys")


if __name__ == "__main__":
    main()
