"""Speech decode() results (JSON per case, + calib.json) -> the flat text the C decode_test reads."""
import json, sys
from pathlib import Path
d = Path(sys.argv[1])
c = json.loads((d / "calib.json").read_text())
(d / "calib.txt").write_text(" ".join(map(repr, c["mean"] + c["std"] + c["weights"] + [c["bias"], c["threshold"]])) + "\n")
for f in sorted(d.glob("[0-9][0-9].json")):
    r = json.loads(f.read_text())
    lines = [f'{r["n"]} {r["reject"]} {r.get("reject_raw", r["reject"])} {int(bool(r.get("veto")))} {r.get("full_len", 0)} {r.get("full_unk", 0)} {r.get("gap", 0.0)!r} {r.get("junk", 1.0)!r}']
    for h in r["h"]:
        lines.append(" ".join([str(len(h["ids"]))] + [f"{i} {c2}" for i, c2 in zip(h["ids"], h["conf"])]))
    f.with_suffix(".txt").write_text("\n".join(lines) + "\n")
