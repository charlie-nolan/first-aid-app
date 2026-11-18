"""Pack trees + slot maps into one FPK1 blob for the firmware form engine (components/vform).

    python formpack.py --vr ~/first-aid-voice/pipeline/voice_router \
        --trees ~/first-aid-build/pipeline/plb/content/trees ~/first-aid-trees/pipeline/plb/content/trees_personal \
        --slots ~/first-aid-voice/slots -o form.fpk

Everything the C engine needs, read in place from flash, no YAML on the device:
the tree's cards (key, label, cites), each card's tree choices (for the walk), its tiles exactly as
vr.intake.tiles() gives them (screen order, values filled in from the slot map), the question each
card asks (vr.intake._questions: slot map `questions:` plus inline `slot:`/`value:`), and the slot
map's words (slots -> values -> phrases, in file order), priority, implies, life rules (short dict
form turned into the ordered list form) and life_words.

The tiles and questions are computed here by the Python reference itself, so the C never has to
redo the slot-map/inline merge. Personal trees make a personal blob: never commit one.

Layout (little endian; every offset is from the start of the blob; NONE = 0xFFFF):
  header  "FPK1" u16 version u16 ntrees u32 nstrings u32 off_stroff u32 off_trees u32 total
  stroff  nstrings x u32 -> NUL-terminated UTF-8 strings (string ids are indices, all interned)
  trees   ntrees x u32 -> TREE
  TREE    u16 name title root nnodes nslots nrules nimplies nlifewords
          u32 off_nodes off_slots off_rules off_implies off_lifewords off_menu        (40 bytes)
  MENU    u16 n, n x u16 slot ids (smap menu_slots, default [missing_kit]: kit branches, never asked),
          then u16 m, m x u16 phrase ids (condition names for the tree, vr.form.topic_phrases' raw
          phrases: smap topic_words + vr.form._DEFAULT_TOPICS[name] + the name when it is a condition),
          then u16 flags (bit 0: generic home of the shared life blanks, vr.live.GENERIC_HOME / smap generic_home)
  NODE    u16 key label qslot nchoices ntiles nqch ncites flags (bit 0: content card, has cite or text)
          u32 off_choices off_tiles off_qch off_cites                                 (32 bytes)
  CHOICE  u16 to label; i16 to_node; u8 has_value kind_next                          (8 bytes)
  TILE    u16 label value to; i16 to_node                                             (8 bytes)
  QCH     u16 value to; i16 to_node; u16 pad                                          (8 bytes)
  SLOT    u16 name nvalues nprio pad; u32 off_values off_prio                         (16 bytes)
  VALUE   u16 name nphrases; u32 off_phrases (u16 string ids)                         (8 bytes)
  RULE    u16 nif nunless go; i16 go_node; u32 off_pairs (u16 slot,value pairs)       (12 bytes)
  IMPLY   u16 slot from to pad                                                        (8 bytes)
  LIFEW   u16 word go                                                                 (4 bytes)
A missing label/value/to is NONE; an empty-but-present label is the string "".
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

import yaml

NONE = 0xFFFF
VERSION = 5


def _s(v) -> str:
    if isinstance(v, bool):
        print(f"warning: YAML boolean {v!r} used as a value (quote it: 'yes'/'no')", file=sys.stderr)
    return str(v)


class Pack:
    def __init__(self):
        self.strings: list[str] = []
        self.ids: dict[str, int] = {}
        self.body = bytearray()

    def sid(self, s) -> int:
        if s is None:
            return NONE
        s = _s(s)
        if s not in self.ids:
            self.ids[s] = len(self.strings)
            self.strings.append(s)
            if len(self.strings) >= NONE:
                raise SystemExit("too many strings for u16 ids")
        return self.ids[s]

    def put(self, data: bytes, align: int = 4) -> int:
        while len(self.body) % align:
            self.body.append(0)
        off = len(self.body)
        self.body += data
        return off


def pack_tree(P: Pack, name: str, tree: dict, smap: dict, vr) -> bytes:
    from vr.intake import _questions, tiles
    nodes = tree["nodes"]
    keys = list(nodes)
    idx = {k: i for i, k in enumerate(keys)}
    node_of = lambda to: idx.get(to, -1) if isinstance(to, str) else -1
    qs = _questions(tree, smap)

    recs = []
    for k in keys:
        n = nodes[k] or {}
        ch = b"".join(struct.pack("<HHhBB", P.sid(c.get("to")) if c.get("to") else NONE,
                                  P.sid(c["label"]) if "label" in c else NONE, node_of(c.get("to")),
                                  1 if c.get("value") else 0, 1 if c.get("kind", "next") == "next" else 0)
                      for c in n.get("choices") or [])
        ts = tiles(tree, smap, k)
        tl = b"".join(struct.pack("<HHHh", P.sid(t["label"]) if "label" in t else NONE,
                                  P.sid(t["value"]) if t.get("value") else NONE,
                                  P.sid(t["to"]), node_of(t["to"])) for t in ts)
        q = qs.get(k)
        qch = b""
        if q:
            qch = b"".join(struct.pack("<HHhH", P.sid(v), P.sid(t) if t else NONE, node_of(t), 0)
                           for v, t in q.get("choices", {}).items())
        cites = [str(c.get("ref", c)) if isinstance(c, dict) else str(c) for c in (n.get("cite") or [])]
        ct = b"".join(struct.pack("<H", P.sid(c)) for c in cites)
        content = 1 if (n.get("cite") or n.get("text")) else 0
        recs.append((P.sid(k), P.sid(n["label"]) if "label" in n else NONE, P.sid(q["slot"]) if q else NONE,
                     len(n.get("choices") or []), len(ts), len(q.get("choices", {})) if q else 0, len(cites), content,
                     P.put(ch), P.put(tl), P.put(qch), P.put(ct)))
    off_nodes = P.put(b"".join(struct.pack("<8H4I", *r) for r in recs))

    slots = smap.get("slots") or {}
    prio = smap.get("priority") or {}
    srecs = []
    for s, values in slots.items():
        vrec = b"".join(struct.pack("<HHI", P.sid(v), len(phr or []),
                                    P.put(b"".join(struct.pack("<H", P.sid(p)) for p in (phr or []))))
                        for v, phr in (values or {}).items())
        pr = list(prio.get(s, []))
        srecs.append(struct.pack("<4H2I", P.sid(s), len(values or {}), len(pr), 0, P.put(vrec),
                                 P.put(b"".join(struct.pack("<H", P.sid(v)) for v in pr))))
    off_slots = P.put(b"".join(srecs))

    lt = smap.get("life_threat") or []
    if isinstance(lt, dict):                          # short form {slot: {value: card}}, same order as vr
        lt = [{"if": {s: v}, "go": card} for s, m in lt.items() for v, card in m.items()]
    rrecs = []
    for r in lt:
        iff, unl = list((r.get("if") or {}).items()), list((r.get("unless") or {}).items())
        pairs = b"".join(struct.pack("<HH", P.sid(s), P.sid(v)) for s, v in iff + unl)
        rrecs.append(struct.pack("<HHHhI", len(iff), len(unl), P.sid(r["go"]), node_of(r["go"]), P.put(pairs)))
    off_rules = P.put(b"".join(rrecs))

    imp = [(s, a, b) for s, m in (smap.get("implies") or {}).items() for a, b in (m or {}).items()]
    off_imp = P.put(b"".join(struct.pack("<4H", P.sid(s), P.sid(a), P.sid(b), 0) for s, a, b in imp))
    lw = list((smap.get("life_words") or {}).items())
    off_lw = P.put(b"".join(struct.pack("<HH", P.sid(w), P.sid(g)) for w, g in lw))

    from vr import intake
    menu = list(smap.get("menu_slots") or getattr(intake, "MENU_SLOTS", ("missing_kit",)))
    from vr import form as F, live as vr_live
    base = name.split("wfa-")[-1]
    topic = [str(ph) for ph in (smap.get("topic_words") or [])] + list(getattr(F, "_DEFAULT_TOPICS", {}).get(name, [])) + \
        ([base] if base in getattr(F, "_NAME_IS_TOPIC", set()) else [])
    off_menu = P.put(struct.pack("<H", len(menu)) + b"".join(struct.pack("<H", P.sid(m)) for m in menu)
                     + struct.pack("<H", len(topic)) + b"".join(struct.pack("<H", P.sid(t)) for t in topic)
                     + struct.pack("<H", 1 if (name in getattr(vr_live, "GENERIC_HOME", set()) or smap.get("generic_home")) else 0),
                     align=2)
    root = idx.get(tree.get("root"), 0)
    return struct.pack("<8H6I", P.sid(name), P.sid(tree.get("title", name)), root, len(keys), len(slots),
                       len(lt), len(imp), len(lw), off_nodes, off_slots, off_rules, off_imp, off_lw, off_menu)


def build(trees: dict[str, tuple[dict, dict]], vr=None) -> bytes:
    """trees: name -> (tree, slot map), in the order the engine should try them (vr.live.load_all order)."""
    P = Pack()
    P.body += b"\0" * 24                            # header, filled in last: offsets are absolute
    P.sid("")                                         # id 0 = ""
    trecs = [pack_tree(P, n, t, s, vr) for n, (t, s) in trees.items()]
    toffs = [P.put(r) for r in trecs]
    trees_at = P.put(b"".join(struct.pack("<I", o) for o in toffs))
    stroff_at = P.put(b"\0" * (4 * len(P.strings)))
    for i, s in enumerate(P.strings):
        struct.pack_into("<I", P.body, stroff_at + 4 * i, P.put(s.encode("utf-8") + b"\0", align=1))
    struct.pack_into("<4sHHIIII", P.body, 0, b"FPK1", VERSION, len(trecs), len(P.strings), stroff_at, trees_at,
                     len(P.body))
    return bytes(P.body)


def load(vr_dir: Path, tree_dirs: list[Path], slots_dir: Path) -> dict:
    sys.path.insert(0, str(Path(vr_dir).expanduser()))
    from vr.live import load_all
    return load_all([Path(d).expanduser() for d in tree_dirs], Path(slots_dir).expanduser())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--vr", type=Path, required=True, help="pipeline/voice_router folder (9a9dc67 or later)")
    ap.add_argument("--trees", type=Path, nargs="+", required=True)
    ap.add_argument("--slots", type=Path, required=True)
    ap.add_argument("-o", "--out", type=Path, required=True)
    a = ap.parse_args(argv)
    trees = load(a.vr, a.trees, a.slots)
    blob = build(trees)
    a.out.write_bytes(blob)
    print(f"{a.out}: {len(trees)} trees, {len(blob)} bytes")


if __name__ == "__main__":
    main()
