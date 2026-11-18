"""A fake tether board on a pseudo-terminal, for testing pc_bridge.py --board-brain without the S3.

It speaks the board side of the link (README.md) and runs the SAME C form engine as the firmware
(components/vform, through its host_test binary), so the bridge's shadow check is exercised end to
end. After every form frame it taps the first choice of the first field, up to --taps per problem.

    cc -O2 -ffp-contract=off -o /tmp/vform_test ../components/vform/host_test.c \\
        ../components/vform/vform.c ../components/vform/vform_show.c -lm
    python fake_board.py --bin /tmp/vform_test --pack /tmp/real.fpk      # prints the pty path
    python pc_bridge.py --board-brain --typed --port <that path> ...      # then type sentences
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tty


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True)
    ap.add_argument("--pack", required=True)
    ap.add_argument("--taps", type=int, default=2)
    ap.add_argument("--reject", type=int, default=70)
    a = ap.parse_args()
    eng = subprocess.Popen([a.bin, a.pack, "-"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    master, slave = os.openpty()
    tty.setraw(slave)
    print(os.ttyname(slave), flush=True)

    def run(cmd: str) -> str:
        eng.stdin.write(cmd + "\n"); eng.stdin.flush()
        return eng.stdout.readline().rstrip("\n")

    def out(line: str):
        os.write(master, (line + "\n").encode())

    taps, buf = 0, b""
    HINT = "HINT Hold TALK and say the answers, or pick with buttons."   # as tether.c's brain task

    def reset():
        run("CASE fake")                    # the engine echoes "CASE fake"
        eng.stdin.write(HINT + "\n"); eng.stdin.flush()

    reset()
    while True:
        buf += os.read(master, 65536)
        while b"\n" in buf:
            ln, buf = buf.split(b"\n", 1)
            ln = ln.decode(errors="replace").strip()
            if ln == "HELLO":
                out("VR-TETHER 1 0")
            elif ln == "BRAIN-RESET":
                reset()
            elif ln.startswith("WORDS "):
                w = json.loads(ln[6:])
                text = (w.get("hyps") or [""])[0].replace("\t", " ")
                fr = run(f"AGAIN {w.get('reject', 0)}\t{text}") if w.get("reject", 0) >= a.reject else run(f"SAY {text}")
                out("SHOWN " + fr)
                d = json.loads(fr)
                if d["kind"] != "form":
                    taps = 0
                while d["kind"] == "form" and d.get("fields") and taps < a.taps:
                    taps += 1
                    f, c = d["fields"][0], d["fields"][0]["choices"][0]
                    p = {"seq": d["seq"], "field": f["key"], "value": c["value"], "label": c["label"]}
                    if c["value"] is None:
                        p["node"] = c["node"]
                    out("PICK " + json.dumps(p, separators=(",", ":")))
                    fr = run(f"PICK {f['key']}\t{c['value'] if c['value'] is not None else chr(92) + 'N'}\t{c.get('node') or chr(92) + 'N'}")
                    out("SHOWN " + fr)
                    d = json.loads(fr)


if __name__ == "__main__":
    main()
