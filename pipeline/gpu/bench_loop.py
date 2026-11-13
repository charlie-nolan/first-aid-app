"""Score the REAL recogniser: play each test clip through a speaker into the Plus's mic
and read what ESP-SR MultiNet heard over USB serial.

The Plus must run the voice bench build (firmware/sdkconfig.voicebench), which listens
when it gets "L <ms>" on serial and prints one "VB ..." line per result.

    python bench_loop.py --clips $WORK/clips --port /dev/ttyACM0 [--limit 300] [--volume 0.8]

Numbered tiles: make the key clips (make_clips.py --keys --out $WORK/keyclips) and run
them against a topic's list, e.g. --tree 0, which holds the numbers plus topic phrases.

Put a small speaker 20-50 cm from the board, in a quiet room (the clips carry their own
noise). Results: <clips>/bench-<time>.jsonl and a Markdown summary next to it.
"""
from __future__ import annotations

import argparse, json, random, time, wave
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
INTER = HERE.parent / "plb" / "build" / "intermediate"


def node_of_cmd() -> tuple[dict, dict]:
    trees = json.loads((INTER / "trees.json").read_text())
    keys = {n["id"]: n["key"] for n in trees["nodes"]}
    by_cmd = {c["cmd_id"]: keys[c["node"]] for c in json.loads((INTER / "phrases.json").read_text())}
    by_text = {c["spoken"].lower(): keys[c["node"]] for c in json.loads((INTER / "phrases.json").read_text())}
    # voice keys (numbered tiles): the firmware reports them as ids -100, -101, ...
    for k, name in enumerate(KEY_NAMES):
        by_cmd[-100 - k] = f"key:{name}"
    return by_cmd, by_text


KEY_NAMES = ["1", "2", "3", "4", "back", "again", "home"]   # = VK_* in firmware/main/ui.h


def read_line(ser, prefix: str, timeout: float) -> str | None:
    end = time.time() + timeout
    while time.time() < end:
        raw = ser.readline().decode(errors="replace").strip()
        if raw.startswith(prefix):
            return raw
    return None


def parse_vb(line: str, by_cmd: dict) -> list[tuple[int, float, str, str | None]]:
    # VB <n> <cmd>:<prob>:<phrase>|<cmd>:<prob>:<phrase>...
    head, _, rest = line[3:].partition(" ")
    out = []
    if head.isdigit() and int(head) > 0:
        for item in rest.split("|"):
            cmd, prob, phrase = (item.split(":", 2) + ["", ""])[:3]
            out.append((int(cmd), float(prob), phrase, by_cmd.get(int(cmd))))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", required=True, type=Path)
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--limit", type=int, default=0, help="random sample of N clips (0 = all)")
    ap.add_argument("--volume", type=float, default=0.8)
    ap.add_argument("--tree", type=int, default=-1, help="voice list to load: -1 home, else tree index")
    ap.add_argument("--seed", type=int, default=3)
    a = ap.parse_args()
    import serial, sounddevice as sd

    by_cmd, _ = node_of_cmd()
    rows = [json.loads(l) for l in (a.clips / "manifest.jsonl").read_text().splitlines()]
    if a.limit:
        random.Random(a.seed).shuffle(rows); rows = rows[:a.limit]
    ser = serial.Serial(a.port, 115200, timeout=0.5)
    time.sleep(1.0); ser.reset_input_buffer()
    ser.write(f"T {a.tree}\n".encode()); read_line(ser, "VB list", 5)

    stamp = time.strftime("%Y%m%d-%H%M")
    out = (a.clips / f"bench-{stamp}.jsonl").open("w")
    tally = defaultdict(Counter)
    for i, r in enumerate(rows):
        with wave.open(str(a.clips / r["wav"])) as wf:
            x = np.frombuffer(wf.readframes(wf.getnframes()), np.int16).astype(np.float32) / 32768
            sr = wf.getframerate()
        ms = int(len(x) / sr * 1000) + 700
        ser.reset_input_buffer()
        ser.write(f"L {ms}\n".encode())
        time.sleep(0.15)                       # mic power-up
        sd.play(x * a.volume, sr); sd.wait()
        line = read_line(ser, "VB ", ms / 1000 + 4) or "VB 0"
        res = parse_vb(line, by_cmd)
        top = res[0][3] if res else None
        if r["off_topic"]:
            outcome = "false" if top else "ignored"
        elif not top:
            outcome = "miss"
        else:
            outcome = "hit" if top in r["want"] else ("near" if top.split("/")[0] == r["want"][0].split("/")[0] else "wrong")
        r.update(heard=[(c, p, ph, k) for c, p, ph, k in res], outcome=outcome)
        out.write(json.dumps(r) + "\n"); out.flush()
        for key in ("all", f"noise:{r['noise']}{r['snr'] or ''}", f"voice:{r['voice']}", f"src:{r['source']}"):
            tally[key][outcome] += 1
        if i % 20 == 0:
            print(f"{i}/{len(rows)} {dict(tally['all'])}", flush=True)
    out.close()

    cols = ["hit", "near", "miss", "wrong", "false", "ignored"]
    md = [f"# ESP-SR bench run {stamp}", "", f"{len(rows)} clips, list {a.tree}, volume {a.volume}.", "",
          "| group | " + " | ".join(cols) + " |", "|---" * (len(cols) + 1) + "|"]
    for k in sorted(tally):
        md.append(f"| {k} | " + " | ".join(str(tally[k][c]) for c in cols) + " |")
    (a.clips / f"bench-{stamp}.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
