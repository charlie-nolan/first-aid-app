"""Cut an EXPT v1 tail (fasr/tail.py format) out of a base ASRM/FQ81 blob: layers a..n-1, unchanged. Only for testing
the device's trunk -> H -> tail path without trained tails: the result must reproduce the base model bit for bit.

    python tailslice.py asrm.bin --layer-a 21 --group test -o tail.expt [--trunk-id 0011223344556677]
"""
import argparse, struct
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("asrm", type=Path); ap.add_argument("--layer-a", type=int, required=True)
    ap.add_argument("--group", default="test"); ap.add_argument("--group-id", type=int, default=0)
    ap.add_argument("--trunk-id", default="00" * 8); ap.add_argument("-o", "--out", type=Path, required=True)
    a = ap.parse_args(argv)
    b = a.asrm.read_bytes()
    assert b[:4] == b"FQ81"
    ver, nl = struct.unpack_from("<HH", b, 4); off = struct.unpack_from("<I", b, 12)[0]
    A = a.layer_a; assert 0 < A < nl
    lay = lambda i: b[336 + 32 * i: 368 + 32 * i]
    e_h = struct.unpack_from("<b", lay(A), 12)[0]                  # the tail's input exponent = its first layer's e_in
    tables = b"".join(lay(i) for i in range(A, nl))
    head = bytearray(b[:336]); struct.pack_into("<H", head, 6, nl - A); struct.pack_into("<h", head, 8, e_h)
    new_off = 336 + len(tables); struct.pack_into("<I", head, 12, new_off)
    fq = bytes(head) + tables + b[off:]                            # weights block kept whole (offsets unchanged)
    hdr = b"EXPT" + struct.pack("<HH", 1, a.group_id) + bytes.fromhex(a.trunk_id) + struct.pack("<HHb3x", A, nl, e_h) \
          + struct.pack("<I", len(fq)) + a.group.encode()[:16].ljust(16, b"\0") + b"\0" * 4
    assert len(hdr) == 48
    a.out.write_bytes(hdr + fq)
    print(f"{a.out}: tail layers {A}..{nl - 1}, e_h {e_h}, {len(hdr) + len(fq)} bytes")


if __name__ == "__main__":
    main()
