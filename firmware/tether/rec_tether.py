"""Training mode for the beta firmware: record speech training clips with the board's mic and buttons while the
prompt shows here (and on the e-ink). No reflashing: the beta firmware has the mode built in.

    ~/miniconda3/envs/firstaid/bin/python firmware/tether/rec_tether.py [--prompts FILE] [--port /dev/ttyACM0]

Hold TALK on the board, say the prompt, let go: the clip is saved exactly as the recorder + `fasr devrec` save
it ($WORK/speech/me_dev/<mode>/, train.jsonl / eval.jsonl / answers.jsonl ...), with fasr.devrec.Store.
Board: DOWN = skip, BACK = record the last one again, UP = previous prompt.
Keyboard: m = next mode, n = skip, p = previous, r = redo last, q = quit (the board goes back to normal).
Prompts: the recorder's prompt file ("T|text" train, "E|text" eval, "P|text" pairs, "A|question|answer" answers).
Modes "free" (say anything; labelled later) and "noise" (background) have no prompts.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import select
import sys
import termios
import time
import tty
import zlib
from pathlib import Path

SPEECH = Path(os.environ.get("FASR", Path.home() / "first-aid-speech/pipeline/speech"))
sys.path.insert(0, str(SPEECH))
from fasr.devrec import OUT, Store  # noqa: E402  (same files and lists as the recorder)

WORK = Path(os.environ.get("WORK", "/mnt/data/first-aid-gpu"))
DEFAULT_PROMPTS = WORK / "speech/local_prompts/prompts-v17-answers-form.txt"
MODES = ["train", "answers", "pairs", "eval", "free", "noise"]
KEY = {"T": "train", "E": "eval", "P": "pairs", "A": "answers"}
STATE = OUT / "rec_tether_state.json"


def load_prompts(path: Path) -> dict[str, list[tuple[str, str]]]:
    """mode -> [(shown text, label)]; for answers the screen shows question + answer, the label is the answer"""
    out: dict[str, list[tuple[str, str]]] = {m: [] for m in MODES}
    for line in path.read_text().splitlines():
        k, _, rest = line.partition("|")
        if k not in KEY or not rest.strip():
            continue
        if k == "A":
            q, _, a = rest.partition("|")
            out["answers"].append((q.strip(), a.strip()))
        else:
            out[KEY[k]].append((rest.strip(), rest.strip()))
    return out


def find_port() -> str | None:
    from serial.tools import list_ports
    ps = [p.device for p in list_ports.comports() if "ACM" in p.device or "usbmodem" in p.device]
    return ps[0] if ps else None


class Rec:
    def __init__(self, prompts: dict, store: Store):
        self.p, self.st = prompts, store
        s = json.loads(STATE.read_text()) if STATE.exists() else {}
        self.mode = s.get("mode", "train")
        self.pos = s.get("pos", {})
        self.last = None            # (mode, dev id, prompt index) of the last saved clip, for redo
        self.redo = None
        self.n = 0
        for m in MODES:             # start each list at its first prompt not recorded yet
            if m not in self.pos:
                done = {r.get("text", "").lower() for r in store.rows.values() if r["mode"] == m}
                lst = self.p.get(m, [])
                self.pos[m] = next((i for i, (_, lab) in enumerate(lst) if lab.lower() not in done), 0)

    def save_state(self):
        STATE.write_text(json.dumps({"mode": self.mode, "pos": self.pos}))

    def cur(self) -> tuple[str, str, int]:
        if self.redo and self.redo[2] >= 0:          # recording the last one again: show that prompt
            m, _, i = self.redo
            q, lab = self.p[m][i]
            return (q, lab if m == "answers" else "", i)
        lst = self.p.get(self.mode, [])
        if not lst:
            return ("Say anything you might say to it." if self.mode == "free" else "Stay quiet: background sound only.", "", -1)
        i = self.pos[self.mode] % len(lst)
        q, lab = lst[i]
        return (q, lab if self.mode == "answers" else "", i)

    def move(self, d: int):
        lst = self.p.get(self.mode, [])
        if lst:
            self.pos[self.mode] = (self.pos[self.mode] + d) % len(lst)
        self.redo = None
        self.save_state()

    def next_mode(self):
        self.mode = MODES[(MODES.index(self.mode) + 1) % len(MODES)]
        self.redo = None
        self.save_state()


def show(r: Rec, status: str = ""):
    q, a, i = r.cur()
    lst = r.p.get(r.mode, [])
    sys.stdout.write("\033[2J\033[H")
    print(f"TRAINING  mode: {r.mode}   prompt {i + 1 if i >= 0 else '-'}/{len(lst) or '-'}   clips this session: {r.n}\n")
    print(f"   {q}\n")
    if a:
        print(f"   answer:  {a}\n")
    if r.redo:
        print("   (recording this one AGAIN: the last clip will be replaced)\n")
    print("Board: hold TALK and say it   DOWN skip   BACK redo last   UP previous")
    print("Keys:  m mode   n skip   p previous   r redo last   q quit\n")
    if status:
        print(status)
    sys.stdout.flush()


def send_prompt(s, r: Rec):
    q, a, i = r.cur()
    clean = lambda t: t.replace("|", "/").replace("\n", " ")[:190]
    s.write(f"PROMPT {r.mode}|{r.n}|{clean(q)}|{clean(a)}\n".encode())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompts", type=Path, default=DEFAULT_PROMPTS)
    ap.add_argument("--port")
    a = ap.parse_args(argv)
    import serial
    prompts = load_prompts(a.prompts) if a.prompts.exists() else {m: [] for m in MODES}
    store = Store()
    r = Rec(prompts, store)
    old = termios.tcgetattr(sys.stdin) if sys.stdin.isatty() else None
    if old:
        tty.setcbreak(sys.stdin.fileno())
    try:
        while True:
            port = a.port or find_port()
            if not port:
                show(r, "Waiting for the board: plug it in and press any button...")
                time.sleep(1)
                continue
            try:
                session(serial.Serial(port, 115200, timeout=0.1), r, store)
                return
            except (serial.SerialException, OSError):
                show(r, "Lost the board; reconnecting...")
                time.sleep(1)
    finally:
        if old:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)


def session(s, r: Rec, store: Store):
    s.write(b"REC-HELLO\n")
    hdr, chunks, last_ping, linked, status = None, [], 0.0, False, "Connecting to the board..."
    show(r, status)
    buf = b""
    while True:
        now = time.time()
        if now - last_ping > 2:
            s.write(b"PING\n" if linked else b"REC-HELLO\n")
            last_ping = now
        if sys.stdin.isatty() and select.select([sys.stdin], [], [], 0)[0]:
            k = sys.stdin.read(1).lower()
            if k == "q":
                s.write(b"REC-BYE\n"); s.flush()
                print("\nBye: the board is back to normal.")
                return
            if k in "mnpr":
                if k == "m": r.next_mode()
                elif k == "n": r.move(+1)
                elif k == "p": r.move(-1)
                elif k == "r" and r.last: r.redo = r.last
                send_prompt(s, r); show(r, "")
        buf += s.read(4096)
        while b"\n" in buf:
            raw, buf = buf.split(b"\n", 1)
            line = raw.decode("ascii", "replace").strip()
            if line.startswith("REC-BETA"):
                if not linked:
                    linked = True
                    send_prompt(s, r)
                    show(r, "Board connected. Hold TALK on the board and say the prompt.")
            elif line.startswith("BTN "):
                b = line[4:].strip()
                if b == "skip": r.move(+1)
                elif b == "back": r.move(-1)
                elif b == "redo" and r.last: r.redo = r.last
                send_prompt(s, r); show(r, "")
            elif line.startswith("TAKE "):
                hdr, chunks = json.loads(line[5:]), []
            elif line.startswith("D ") and hdr is not None:
                chunks.append(base64.b64decode(line[2:]))
            elif line.startswith("END ") and hdr is not None:
                pcm = b"".join(chunks)
                ok = len(pcm) == 2 * hdr["samples"] and format(zlib.crc32(pcm), "08x") == line[4:].strip()
                if not ok:
                    s.write(b"RESEND\n"); hdr = None; continue
                redo = r.redo
                mode = redo[0] if redo else r.mode
                q, lab, i = (r.p[mode][redo[2]][0], r.p[mode][redo[2]][1], redo[2]) if redo and redo[2] >= 0 else r.cur()
                if mode != "answers":
                    lab = q if mode in ("train", "eval", "pairs") else ""
                hdr.update({"mode": mode, "prompt": i, "text": lab, "redo_of": redo[1] if redo else -1, "speaker": "me"})
                store.add(hdr, pcm)
                s.write(f"ACK {hdr['id']}\n".encode())
                r.n += 1
                r.last = (mode, hdr["id"], i)
                secs = hdr["samples"] / hdr["sr"]
                warn = "  TOO LOUD (move the mic away)" if hdr["clip_per_mille"] > 5 else "  very quiet?" if hdr["peak"] < 800 else ""
                if not redo:
                    r.move(+1)
                r.redo = None
                send_prompt(s, r)
                show(r, f"Saved #{hdr['id']} ({secs:.1f} s){' (redo)' if redo else ''}: {lab or mode}{warn}")
                s.write(f"STATUS Saved {secs:.1f} s. Next one: hold TALK\n".encode())
                hdr = None


if __name__ == "__main__":
    main()
