"""PC side of the voice tether (firmware/tether): the board's mic and screen, the PC's brain.

The board sends each TALK clip over USB; this bridge saves it, decodes it with the speech model
(fasr), runs the fill-in-the-blanks form (vr.form) and sends back one SHOW screen. Button answers
come back as PICK lines. Every screen the board draws also comes back as a framebuffer, saved as a
PNG, so each bench run leaves real screenshots.

    WORK=/mnt/data/first-aid-gpu python firmware/tether/pc_bridge.py \\
        --vr  <voice_router root at 98d75eb or later> \\
        --trees ~/first-aid-build/pipeline/plb/content/trees ~/first-aid-trees/pipeline/plb/content/trees_personal \\
        --slots ~/first-aid-voice/slots --fasr ~/first-aid-speech/pipeline/speech \\
        [--port /dev/ttyACM0] [--out $WORK/speech/tether] [--typed]

--typed reads sentences from stdin instead of clips (same screens go to the board), handy to test
the screens without speaking. Format of SHOW / PICK: firmware/tether/README.md.
Nothing here is committed from a run: clips, logs and screenshots go to --out (outside the repo).
"""
from __future__ import annotations

import argparse
import base64
import binascii
import datetime as dt
import json
import os
import sys
import threading
import time
import wave
from pathlib import Path

import serial

W, H = 250, 122


class Board:
    def __init__(self, port: str):
        self.s = serial.Serial(port, 115200, timeout=0.2)
        self.lock = threading.Lock()
        self.buf = b""

    def send(self, line: str):
        with self.lock:
            self.s.write((line + "\n").encode("ascii", "replace"))
            self.s.flush()

    def lines(self):
        while True:
            d = self.s.read(8192)
            if not d:
                yield None
                continue
            self.buf += d
            while b"\n" in self.buf:
                ln, self.buf = self.buf.split(b"\n", 1)
                yield ln.decode("ascii", "replace").rstrip("\r")


def save_png(bits: bytes, path: Path):
    from PIL import Image
    img = Image.new("L", (W, H), 255)
    px = img.load()
    for y in range(H):
        for x in range(W):
            if bits[y * 32 + x // 8] & (0x80 >> (x % 8)):
                px[x, y] = 0
    img.resize((W * 3, H * 3), Image.NEAREST).save(path)


class Scenarios:
    """The scenario list Charlie acts out (hidden gold stays hidden: only the prompt text is shown).
    Reloaded when the file changes, since it may still be being written. BACK hold on the board
    (new problem) moves to the next one; in the terminal: Enter/n = next, p = previous, a number = jump."""
    TEXT_KEYS = ("say", "scenario", "text", "situation", "prompt", "story", "description")

    def __init__(self, path: Path | None):
        self.path, self.items, self.i, self.mtime = path, [], 0, None
        self.reload()

    def reload(self):
        if not self.path or not self.path.exists():
            return
        m = self.path.stat().st_mtime
        if m == self.mtime:
            return
        import yaml
        try:
            d = yaml.safe_load(self.path.read_text())
        except Exception as e:                     # half-written file: keep the old list
            print(f"(scenarios not readable yet: {e})", flush=True)
            return
        self.mtime = m
        if isinstance(d, dict):
            d = d["scenarios"] if isinstance(d.get("scenarios"), list) else [
                {"id": k, **v} if isinstance(v, dict) else {"id": k, "text": v} for k, v in d.items()]
        self.items = [x for x in (d or []) if isinstance(x, (dict, str))]

    def current(self) -> tuple[str | None, str]:
        self.reload()
        if not self.items:
            return None, "(no scenarios loaded)" if self.path else ""
        x = self.items[self.i % len(self.items)]
        if isinstance(x, str):
            return f"#{self.i + 1}", x
        sid = str(x.get("id", f"#{self.i + 1}"))
        txt = next((str(x[k]) for k in self.TEXT_KEYS if x.get(k)), "(no text field)")
        return sid, txt

    def move(self, d: int = 1, to: int | None = None):
        self.i = (to if to is not None else self.i + d) % max(1, len(self.items))
        self.print()

    def print(self):
        sid, txt = self.current()
        if sid or txt:
            n = len(self.items)
            print(f"\n==================== SCENARIO {sid} ({self.i % max(1, n) + 1}/{n}) ====================\n"
                  f"  {txt}\n  (act it out on the board; BACK hold = next scenario)\n", flush=True)


class Brain:
    """Speech + vr.parity.Screen (the frame rules shared with the C port and the parity set).
    The bridge adds what Screen leaves out: the reject gate, the board's actions and the turn log."""

    def __init__(self, a):
        sys.path.insert(0, str(Path(a.vr).expanduser()))
        try:
            from vr import form, live, parity
        except ImportError:
            sys.exit("--vr is older than 98d75eb (no vr.parity): update it")
        if not hasattr(form, "node_id"):
            sys.exit("--vr is older than 98d75eb (no vr.form.node_id): update it")
        self.form, self.live = form, live
        self.trees = live.load_all(a.trees, a.slots)
        print(f"{len(self.trees)} trees with blanks loaded", flush=True)
        self.scr = parity.Screen(self.trees)
        self.speech = None if a.typed else live.Speech(a.fasr, a.run)
        self.reject = a.reject

    @property
    def cands(self):
        return self.scr.cands

    def said(self, hyps: list[str], reject: int, rec: dict):
        rec.update(hyps=hyps, reject=reject)
        text = hyps[0] if hyps else ""
        if self.speech and reject >= self.reject:
            self.scr.heard = text
            return self.scr.show("again", msg=f"Didn't catch that (reject {reject}). Hold TALK and say it again.")
        if self.scr.cands is None and text.strip().lower() not in ("", "new", "home", "back", "multi"):
            rec["kept"] = [[c.name, round(c.p, 2)] for c in self.live.keep_trees([text], self.trees)]   # for the log
        return self.scr.said(text)

    def picked(self, p: dict):
        act = p.get("action")
        if act == "new":
            self.scr.cands, self.scr.heard, self.scr.multi = None, "", False
            return self.scr.show("start")
        if act == "open" and p.get("node"):
            name, _, card = p["node"].partition("/")
            return self.scr.card_screen(name, card)
        if act == "skip":
            if self.scr.cands is None:
                return self.scr.show("start", msg="Hold TALK and say what happened.")
            return self.scr.result("skip", None, self.form.skip(self.scr.cands))
        if p.get("field"):
            return self.scr.picked(p)
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pc_bridge")
    ap.add_argument("--vr", type=Path, required=True, help="voice_router folder (holds vr/), 98d75eb or later")
    ap.add_argument("--trees", type=Path, nargs="+", required=True)
    ap.add_argument("--slots", type=Path, required=True)
    ap.add_argument("--fasr", type=Path, help="speech package root (holds fasr/)")
    ap.add_argument("--run", default="firstaid")
    ap.add_argument("--reject", type=int, default=70)
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--out", type=Path, default=Path(os.environ.get("WORK", "/mnt/data/first-aid-gpu")) / "speech" / "tether")
    ap.add_argument("--typed", action="store_true", help="type sentences instead of speaking")
    ap.add_argument("--scenarios", type=Path,
                    default=Path(os.environ.get("WORK", "/mnt/data/first-aid-gpu")) / "speech" / "me_dev" / "scenarios" / "batch1.yaml")
    ap.add_argument("--start", type=int, default=1, help="first scenario number")
    a = ap.parse_args(argv)
    if not a.typed and not a.fasr:
        ap.error("--fasr is needed unless --typed")

    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    out = a.out / stamp
    (out / "clips").mkdir(parents=True, exist_ok=True)
    (out / "screens").mkdir(exist_ok=True)
    log = open(out / "log.jsonl", "a")
    turns = open(out / "turns.jsonl", "a")       # same shape as vr.live_web's turns.jsonl
    scen = Scenarios(a.scenarios)
    scen.i = a.start - 1
    brain = Brain(a)
    board = Board(a.port)
    board.send("HELLO")
    print(f"bridge up: {a.port}, run folder {out}", flush=True)

    def emit(screen, rec=None):
        if not screen:
            return
        board.send("SHOW " + json.dumps(screen, separators=(",", ":")))
        rec = rec or {}
        rec.update(t=time.time(), screen=screen)
        log.write(json.dumps(rec) + "\n"); log.flush()
        s = screen
        desc = s["kind"]
        if s["kind"] == "form":
            desc += ": " + " / ".join(f["q"] for f in s.get("fields", []))
        elif s["kind"] == "card":
            desc += ": " + s["card"]["title"]
        elif s["kind"] == "skip":
            desc += ": " + "; ".join(c["title"] for c in s.get("cards", []))
        print(f"  -> SHOW {s['seq']} {desc}", flush=True)
        sid, _ = scen.current()
        t = {k: rec[k] for k in ("source", "hyps", "reject", "clip", "turn", "kept", "pick") if k in rec}
        t.update(t=rec["t"], scenario=sid, seq=s["seq"], screen=s["kind"])
        if s["kind"] == "form":
            t["form"] = {"heard": {k: v for c in (brain.cands or []) for k, v in c.filled.items()},
                         "fields": [{"question": f["q"], "key": f["key"], "choices": [c["label"] for c in f["choices"]],
                                     "when": f["after"]} for f in s["fields"]],
                         "top3": [c["title"] for c in s.get("top", [])]}
        elif s["kind"] == "skip":
            t["skip"] = [c["title"] for c in s["cards"]]
        elif s["kind"] == "card":
            t["card"] = s["card"]["node"]
        turns.write(json.dumps(t) + "\n"); turns.flush()

    def keys():
        if a.typed:
            print("type a sentence (\"new\" = start over; \":n\" / \":p\" / \":12\" = scenario):", flush=True)
        for ln in sys.stdin:
            ln = ln.strip()
            cmd = ln[1:] if a.typed and ln.startswith(":") else (ln if not a.typed else None)
            if cmd is not None:
                if cmd in ("", "n"):
                    scen.move(+1)
                elif cmd == "p":
                    scen.move(-1)
                elif cmd.isdigit():
                    scen.move(to=int(cmd) - 1)
                continue
            rec = {"source": "typed", "typed": ln}
            emit(brain.said([ln], 0, rec), rec)
    threading.Thread(target=keys, daemon=True).start()
    scen.print()

    take, data, nfb, last_hello = None, [], 0, time.time()
    for ln in board.lines():
        if ln is None:
            if time.time() - last_hello > 3 and take is None:   # board reset: say hello again
                board.send("HELLO"); last_hello = time.time()
            continue
        last_hello = time.time()
        if ln.startswith("VR-TETHER"):
            print("board:", ln, flush=True)
        elif ln.startswith("TAKE "):
            take, data = json.loads(ln[5:]), []
        elif ln.startswith("D ") and take is not None:
            data.append(base64.b64decode(ln[2:]))
        elif ln.startswith("END ") and take is not None:
            pcm = b"".join(data)
            if binascii.crc32(pcm) & 0xFFFFFFFF != int(ln[4:], 16) or len(pcm) != 2 * take["samples"]:
                print("  bad transfer, asking again", flush=True)
                board.send("RESEND"); take = None
                continue
            wav = out / "clips" / f"take-{take['id']:05d}.wav"
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(take["sr"]); w.writeframes(pcm)
            board.send(f"ACK {take['id']}")
            print(f"\n[{wav.name}] {take['samples'] / take['sr']:.1f} s, peak {take['peak']}", flush=True)
            rec = {"source": "board", "clip": str(wav), "take": take}
            if brain.speech:
                hyps, reject = brain.speech(wav)
                print(f"  heard: {hyps[0]!r} (reject {reject})", flush=True)
                emit(brain.said(hyps, reject, rec), rec)
            take = None
        elif ln.startswith("PICK "):
            p = json.loads(ln[5:])
            print(f"  PICK {p}", flush=True)
            rec = {"source": "pick", "pick": p}
            emit(brain.picked(p), rec)
            if p.get("action") == "new":
                scen.move(+1)
        elif ln.startswith("FB "):
            try:
                bits = base64.b64decode(ln[3:])
            except binascii.Error:
                continue
            if len(bits) == 32 * H:
                nfb += 1
                save_png(bits, out / "screens" / f"screen-{nfb:03d}.png")
        elif ln.startswith("LOG "):
            log.write(json.dumps({"t": time.time(), "board": ln[4:]}) + "\n"); log.flush()


if __name__ == "__main__":
    main()
