"""TRNK chunk (the base model's trunk id, so the device refuses a tail made for another trunk), from an int8 export's
model.json["trunk"] (speech b5d24c9+): {split_block, layer_a, e_h, trunk_bytes, trunk_id}.

    python trnkpack.py <export dir> -o trnk.bin       then: cart_add.py ... TRNK=trnk.bin
Layout: "TRNK" u16 split_block u16 layer_a i8 e_h u8 pad u8 trunk_id[8]   (18 bytes)"""
import argparse, json, struct
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("export", type=Path); ap.add_argument("-o", "--out", type=Path, required=True)
    a = ap.parse_args(argv)
    t = json.loads((a.export / "model.json").read_text()).get("trunk")
    if not t: raise SystemExit("this export has no trunk field (needs speech b5d24c9 or later)")
    tid = bytes.fromhex(t["trunk_id"]) if isinstance(t["trunk_id"], str) else bytes(t["trunk_id"])
    assert len(tid) == 8
    a.out.write_bytes(b"TRNK" + struct.pack("<HHbB", t["split_block"], t["layer_a"], t["e_h"], 0) + tid)
    print(f"{a.out}: split {t['split_block']}, layer_a {t['layer_a']}, e_h {t['e_h']}, trunk_id {tid.hex()}")


if __name__ == "__main__":
    main()
