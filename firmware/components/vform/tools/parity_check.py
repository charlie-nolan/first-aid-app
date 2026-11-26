"""Replay a vr.parity set through the C engine and compare every SHOW frame byte for byte.

    python parity_check.py --bin /tmp/vform_test --pack form.fpk --parity parity_<commit>.jsonl [--fuzz]

The parity set (vr/parity.py) holds, per case, the first sentence, the answers (spoken text or taps)
and the frames the Python sent. The C gets the same sentence and answers; its frames must equal
json.dumps(frame, separators=(",", ":")) exactly. Personal trees: keep the outputs local.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def esc(s):
    return "\\N" if s is None else str(s).replace("\t", " ").replace("\n", " ")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", help="host test binary (host_test.c)")
    ap.add_argument("--pack", help="FPK1 pack for --bin")
    ap.add_argument("--frames", type=Path, help="compare frames already captured (the board's serial log) instead")
    ap.add_argument("--write-script", type=Path, help="write the replay script (for the board bench) and stop")
    ap.add_argument("--parity", type=Path, required=True)
    ap.add_argument("--fuzz", action="store_true", help="also run the tap-loop finder on every sentence")
    ap.add_argument("--show", type=int, default=5, help="mismatches to print")
    a = ap.parse_args(argv)
    cases = [json.loads(l) for l in a.parity.read_text().splitlines() if l.strip()]
    script = []
    for c in cases:
        script.append(f"CASE {c['id']}")
        script.append(f"SAY {esc(c['text'])}")
        for ans in c["answers"]:
            if "text" in ans:
                script.append(f"SAY {esc(ans['text'])}")
            else:
                p = ans["pick"]
                if p.get("clear"): script.append(f"CLEAR {esc(p['field'])}")
                else: script.append(f"PICK {esc(p['field'])}\t{esc(p.get('value'))}\t{esc(p.get('node'))}")
    if a.fuzz:
        for t in dict.fromkeys(c["text"] for c in cases):
            script.append(f"FUZZ {esc(t)}")
    if a.write_script:
        a.write_script.write_text("\n".join(script) + "\n")
        print(f"{a.write_script}: {len(script)} lines")
        return 0
    if a.frames:
        text = a.frames.read_text(errors="replace")
    else:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("\n".join(script) + "\n")
        out = subprocess.run([a.bin, a.pack, f.name], capture_output=True, text=True)
        if out.returncode:
            print(out.stderr); sys.exit(out.returncode)
        text = out.stdout
    got, cur, tail = {}, None, []
    for line in text.splitlines():
        line = line.rstrip("\r")
        if line.startswith("CASE "):
            cur = line[5:]; got[cur] = []
        elif line.startswith("{") and cur is not None:
            got[cur].append(line)
        else:
            tail.append(line)
    frames = bad_cases = bad_frames = 0
    shown = 0
    for c in cases:
        want = [json.dumps(fr, separators=(",", ":")) for fr in c["frames"]]
        have = got.get(c["id"], [])
        frames += len(want)
        diff = [i for i in range(max(len(want), len(have)))
                if i >= len(want) or i >= len(have) or want[i] != have[i]]
        if diff:
            bad_cases += 1; bad_frames += len(diff)
            if shown < a.show:
                shown += 1
                i = diff[0]
                print(f"MISMATCH {c['id']} frame {i + 1}: {c['text']!r} answers {c['answers']}")
                print(f"  python: {want[i] if i < len(want) else '(none)'}")
                print(f"  c:      {have[i] if i < len(have) else '(none)'}")
    print(f"{len(cases)} cases, {frames} frames: {len(cases) - bad_cases} cases identical, "
          f"{bad_cases} differ ({bad_frames} frames)")
    for l in tail:
        print(l)
    return 1 if bad_cases else 0


if __name__ == "__main__":
    sys.exit(main())
