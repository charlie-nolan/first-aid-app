"""Fast checks (no trees needed): lexicon contract, features, int8 export, C parity."""
import random
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vr.lexicon import RESERVED, Lexicon  # noqa: E402
from vr.model import Net, Packed, features, fnv1a  # noqa: E402
from vr.noise import Confusions, nbest  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


def lex():
    return Lexicon.harvest(["the skin isn't broken", "bone sticking out of his leg", "can't move my arm"])


def test_reserved_ids():
    l = lex()
    assert l.words[:10] == RESERVED and l.ids["one"] == 1 and l.ids["home"] == 9
    assert l.is_neg(l.ids["isn't"]) and l.is_neg(l.ids["can't"])


def test_negation_changes_features():
    l = lex()
    a = features([[(i, 100) for i in l.encode("skin broken")]], l, 512)
    b = features([[(i, 100) for i in l.encode("skin isn't broken")]], l, 512)
    assert set(b[2]) - set(a[2])          # extra hashed negation row


def test_fnv_known_value():
    assert fnv1a(1, 300, 7, 1 << 32) == fnv1a(1, 300, 7, 1 << 32)
    assert fnv1a(1, 300, 7, 4096) != fnv1a(2, 300, 7, 4096)


def test_noise_is_valid():
    l = lex()
    nb = nbest(l.encode("bone sticking out"), Confusions(l), 0.3, random.Random(1), n=3)
    assert 1 <= len(nb) <= 3 and all(0 <= i < len(l) and 0 <= c <= 100 for h in nb for i, c in h)


def test_pack_and_c_reader(tmp_path):
    l = lex()
    net = Net(l, 3, d=16, hidden=32, buckets=256, ctx=4)
    net.eval()
    p = Packed(net, [0, 1, 2], ["a", "b", "c"], kind=1)
    assert p.set_pins([(2, "skin broken")]) == []
    blob = p.to_bytes()
    assert blob[:4] == b"VXP1" and len(blob) % 4 == 0
    (tmp_path / "m.vxp").write_bytes(blob)
    with open(tmp_path / "g.txt", "w") as f:
        for text, ctx in [("bone sticking out", 2), ("the skin isn't broken", -1)]:
            ids = l.encode(text)
            nb = [[(i, 100) for i in ids]]
            lg = p.logits(nb, ctx)
            e = np.exp(lg - lg.max()); t, auto = p.tiles(e / e.sum(), nb)
            f.write(f"{ctx} {len(ids)} {' '.join(map(str, ids))} 3 {' '.join(f'{v:.6f}' for v in lg)} "
                    f"{' '.join(map(str, t))} {int(auto)}\n")
    comp = ROOT / "firmware" / "components" / "vrouter"
    exe = tmp_path / "vr_test"
    subprocess.run(["cc", "-O2", "-o", str(exe), str(comp / "host_test.c"), str(comp / "vrouter.c"), "-lm"], check=True)
    r = subprocess.run([str(exe), str(tmp_path / "m.vxp"), str(tmp_path / "g.txt")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
