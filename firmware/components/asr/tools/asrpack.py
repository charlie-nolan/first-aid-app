"""Speech export (FASR-Q8 v1: model.json + model.bin) -> ASRM chunk for the cart (tools/cart_add.py).

    python asrpack.py $WORK/speech/export/<commit> -o asrm.bin

Layout (little endian), read in place by fasr_q8.c:
  "FQ81" u16 version(1) u16 n_layers  i16 e_in  u16 reserved  u32 off_weights
  float mu[40], float sd[40]
  n_layers x LAYER (32 bytes):
     u16 cin cout k stride pad groups; i8 e_in e_out e_res; u8 flags (1 relu, 2 residual, 4 head);
     u32 w_off b_off ew_off  (into the weights block, which is model.bin as is, 16-byte aligned)
  weights block at off_weights (16-byte aligned)
"""
import argparse, json, struct
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("export", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True)
    a = ap.parse_args(argv)
    meta = json.loads((a.export / "model.json").read_text())
    if meta.get("format") != "FASR-Q8 v1":
        raise SystemExit(f"unknown format {meta.get('format')!r}")
    L = meta["layers"]
    head = b"FQ81" + struct.pack("<HHhHI", 1, len(L), meta["e_in"], 0, 0)
    head += struct.pack("<40f", *meta["mu"]) + struct.pack("<40f", *meta["sd"])
    for l in L:
        flags = (1 if l["relu"] else 0) | (2 if l["residual"] else 0) | (4 if l["head"] else 0)
        head += struct.pack("<6H3bB3I4x", l["cin"], l["cout"], l["k"], l["stride"], l["pad"], l["groups"],
                            l["e_in"], l["e_out"] if l["e_out"] is not None else 0, l.get("e_res", 0), flags,
                            l["w_off"], l["b_off"], l["e_w_off"])
    off = (len(head) + 15) // 16 * 16
    blob = bytearray(head + b"\0" * (off - len(head)))
    struct.pack_into("<I", blob, 12, off)
    blob += (a.export / "model.bin").read_bytes()
    a.out.write_bytes(bytes(blob))
    print(f"{a.out}: {len(L)} layers, {len(blob)} bytes (commit {meta.get('commit')}, run {meta.get('run')})")


if __name__ == "__main__":
    main()
