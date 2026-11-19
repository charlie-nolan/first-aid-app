"""Speech lexicon + decoder trie -> LEXI chunk for the board (fasr_decode.c reads it in place).

    python lexpack.py --fasr <pipeline/speech at the export's commit> -o lexi.bin [--lexicon lexicon.yaml]

The trie is built by fasr's own Trie.from_lexicon, so node numbers and word order match the
Python decoder exactly. Layout (little endian, offsets from the blob start):
  "LEX2" u16 version(2) u16 n_tokens  u32 n_ids  u32 n_nodes  u8 blank u8 sep u16 reserved
  f64 beam tok_prune word_bonus implicit_sep garbage_enter garbage_phone reject_gap0 reject_gap_scale
  u32 life_veto_conf  u32 off_words off_cls off_flags off_bias off_nodes        (header: 112 bytes)
  words:  n_ids x u32 offset (0 = none) -> NUL-terminated word
  cls:    n_ids x u8 word class;  flags: n_ids x u8 (1 = FILLER word);  bias: n_ids x f64 (life bias)
  nodes:  n_nodes x {u8 tok, u8 n_child, u16 n_words, u32 off_child, u32 off_wordids}  (12 bytes)
          child: n_child x {u16 tok, u16 pad, u32 node};  wordids: n_words x u16
"""
import argparse, struct, sys
from pathlib import Path
import yaml


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fasr", type=Path, required=True, help="pipeline/speech folder (holds fasr/)")
    ap.add_argument("--lexicon", type=Path, help="default: fasr's own lexicon.yaml")
    ap.add_argument("-o", "--out", type=Path, required=True)
    a = ap.parse_args(argv)
    sys.path.insert(0, str(a.fasr))
    from fasr import lexicon as lx
    from fasr.decode import FILLER, Params, Trie, life_bias
    from fasr.phones import BLANK, N_TOKENS, SEP
    rows = yaml.safe_load((a.lexicon or lx.LEXICON).read_text())["words"]
    trie, p = Trie.from_lexicon(rows), Params(bias=life_bias(rows))
    n = max(r["id"] for r in rows) + 1
    word = {r["id"]: str(r["word"]) for r in rows}
    blob = bytearray(112)
    def put(b, align=4):
        while len(blob) % align: blob.append(0)
        o = len(blob); blob.extend(b); return o
    # words
    off_words = put(b"\0" * (4 * n))
    for i in range(1, n):
        if i in word:
            struct.pack_into("<I", blob, off_words + 4 * i, put(word[i].encode() + b"\0", 1))
    off_cls = put(bytes(int(trie.cls.get(i, 0)) & 0xFF for i in range(n)))
    off_flags = put(bytes(1 if trie.name.get(i) in FILLER else 0 for i in range(n)))
    off_bias = put(struct.pack(f"<{n}d", *[float(p.bias.get(i, 0.0)) for i in range(n)]))
    nn = len(trie.child)
    off_nodes = put(b"\0" * (12 * nn))
    for k in range(nn):
        ch = list(trie.child[k].items())
        oc = put(b"".join(struct.pack("<HHI", t, 0, c) for t, c in ch)) if ch else 0
        ow = put(struct.pack(f"<{len(trie.words[k])}H", *trie.words[k]), 2) if trie.words[k] else 0
        struct.pack_into("<BBHII", blob, off_nodes + 12 * k, trie.tok[k] & 0xFF, len(ch), len(trie.words[k]), oc, ow)
    struct.pack_into("<4sHHIIBBH8dI5I", blob, 0, b"LEX2", 2, N_TOKENS, n, nn, BLANK, SEP, 0,
                     p.beam, p.tok_prune, p.word_bonus, p.implicit_sep, p.garbage_enter, p.garbage_phone,
                     p.reject_gap0, p.reject_gap_scale, p.life_veto_conf, off_words, off_cls, off_flags, off_bias, off_nodes)
    a.out.write_bytes(bytes(blob))
    print(f"{a.out}: {n} ids, {len(word)} words, {nn} trie nodes, {len(blob)} bytes")


if __name__ == "__main__":
    main()
