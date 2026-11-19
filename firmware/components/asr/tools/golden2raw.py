"""golden/NN.npz (speech export) -> flat files the C host test reads:
NN.feats (u32 T, u32 40, float32 T*40), NN.xq / NN.out_<layer> (u32 C, u32 T, int8 C*T),
NN.logits (u32 C, u32 T, int32), NN.logp (u32 T, u32 C, float32)."""
import sys, struct
from pathlib import Path
import numpy as np

src, dst = Path(sys.argv[1]), Path(sys.argv[2]); dst.mkdir(parents=True, exist_ok=True)
for f in sorted(src.glob("*.npz")):
    z = np.load(f, allow_pickle=False)
    def put(name, a, dt):
        a = np.ascontiguousarray(a.astype(dt))
        (dst / f"{f.stem}.{name}").write_bytes(struct.pack("<II", *a.shape) + a.tobytes())
    put("feats", z["feats"], "<f4"); put("xq", z["x_q"], "i1"); put("logits", z["logits"], "<i4"); put("logp", z["logp"], "<f4")
    for k in z.files:
        if k.startswith("out_"):
            put(k, z[k], "i1")
    if "audio" in z.files:
        a = z["audio"].astype("<i2"); (dst / f"{f.stem}.audio").write_bytes(struct.pack("<I", len(a)) + a.tobytes())
print("done", len(list(src.glob('*.npz'))), "clips")
