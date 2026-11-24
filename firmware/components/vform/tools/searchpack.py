"""Keyword-search index (vr.search.build_index, the Python reference itself) -> SRCH chunk for the cart.

    python searchpack.py --vr <voice_router at 6244a99+> --trees DIR [DIR] --slots DIR -o srch.bin

Per node the packer stores each word's weight as vr.search scores it (sum over sources of the best
weight per source), so the C only adds, ranks and pages (vform_search.c). Personal: never commit.
Layout (little endian, offsets from the blob start):
  "SR01" u16 version(1) u16 n_trees u32 n_keys u32 n_nodes u32 off_keys u32 off_trees u32 off_nodes
  u32 off_aliases u32 n_aliases u32 off_stop u32 n_stop                            (header 48 bytes)
  keys:    n_keys x u32 offset -> NUL-terminated key, sorted by bytes (binary search)
  trees:   n_trees x {u32 n_topic, u32 off_topic (u32 key ids), u32 n_names, u32 off_names}
           names: n_names x {u32 n, u32 off (u32 key ids)}
  nodes:   n_nodes x {u16 tree, u8 life, u8 root, u16 depth, u16 nwords, u32 order, u32 off_node,
                      u32 off_title, u32 off_words}  (24 bytes); words: nwords x {u32 key id, u32 weight}
           order = rank of (tree name, node id) in Python's sort; node = node_id string; title = "<tree> > <label>"
  aliases: n x {u32 key, u32 n, u32 off (u32 key ids)};  stop: n x u32 offset -> stop word (vr.search.STOP)
"""
import argparse, struct, sys
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--vr", type=Path, required=True)
    ap.add_argument("--trees", type=Path, nargs="+", required=True)
    ap.add_argument("--slots", type=Path, required=True)
    ap.add_argument("-o", "--out", type=Path, required=True)
    a = ap.parse_args(argv)
    sys.path.insert(0, str(a.vr))
    from vr import search as S
    from vr.form import node_id, _key
    from vr.live import load_all
    trees = load_all(a.trees, a.slots)
    idx = S.build_index(trees)
    tnames = list(trees)
    keys = set()
    for e in idx.values():
        keys |= set(e["words"]) | set(e["topic"])
        for t in e["names"]: keys |= set(t)
    alias = {_key(k) if False else k: v for k, v in S.ALIASES.items()}
    for k, v in alias.items(): keys |= {k, *v}
    keys = sorted(keys, key=lambda s: s.encode())
    kid = {k: i for i, k in enumerate(keys)}
    blob = bytearray(48)
    def put(b, align=4):
        while len(blob) % align: blob.append(0)
        o = len(blob); blob.extend(b); return o
    def sput(s): return put(s.encode() + b"\0", 1)
    off_keys = put(b"\0" * 4 * len(keys))
    for i, k in enumerate(keys): struct.pack_into("<I", blob, off_keys + 4 * i, sput(k))
    # trees: topic and names are per tree (same for all its nodes)
    trec = []
    for name in tnames:
        e = next(v for (n, _), v in idx.items() if n == name)
        tp = sorted(kid[k] for k in e["topic"])
        o_tp = put(struct.pack(f"<{len(tp)}I", *tp)) if tp else 0
        nm = []
        for t in e["names"]:
            ids = sorted(kid[k] for k in t)
            nm.append((len(ids), put(struct.pack(f"<{len(ids)}I", *ids))))
        o_nm = put(b"".join(struct.pack("<II", *x) for x in nm)) if nm else 0
        trec.append(struct.pack("<4I", len(tp), o_tp, len(nm), o_nm))
    off_trees = put(b"".join(trec))
    order = {k: i for i, k in enumerate(sorted(idx))}       # (tree name, node id) tuple order
    nrec = []
    for (name, nid), e in idx.items():
        words = sorted((kid[k], sum(src.values())) for k, src in e["words"].items())
        o_w = put(b"".join(struct.pack("<II", *x) for x in words)) if words else 0
        tree = trees[name][0]
        nrec.append(struct.pack("<HBBHHIIII", tnames.index(name), 1 if e["life"] else 0, 1 if e["root"] else 0,
                                min(e["depth"], 65535), len(words), order[(name, nid)],
                                sput(node_id(name, nid, tree)), sput(f"{e['tree']} > {e['label']}"), o_w))
    off_nodes = put(b"".join(nrec))
    arec = []
    for k, v in alias.items():
        ids = [kid[x] for x in v]
        arec.append(struct.pack("<III", kid[k], len(ids), put(struct.pack(f"<{len(ids)}I", *ids))))
    off_alias = put(b"".join(arec)) if arec else 0
    stop = sorted(S.STOP)
    off_stop = put(b"\0" * 4 * len(stop))
    for i, w in enumerate(stop): struct.pack_into("<I", blob, off_stop + 4 * i, sput(w))
    struct.pack_into("<4sHHIIIIIIIII", blob, 0, b"SR01", 1, len(tnames), len(keys), len(idx), off_keys, off_trees,
                     off_nodes, off_alias, len(arec), off_stop, len(stop))
    a.out.write_bytes(bytes(blob))
    print(f"{a.out}: {len(tnames)} trees, {len(idx)} nodes, {len(keys)} keys, {len(blob)} bytes")


if __name__ == "__main__":
    main()
