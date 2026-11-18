"""PC side of the voice tether (firmware/tether): the board's mic and screen, the PC's brain.

The board sends each TALK clip over USB; this bridge saves it, decodes it with the speech model
(fasr), runs the fill-in-the-blanks form (vr.form) and sends back one SHOW screen. Button answers
come back as PICK lines. Every screen the board draws also comes back as a framebuffer, saved as a
PNG, so each bench run leaves real screenshots.

    WORK=/mnt/data/first-aid-gpu python firmware/tether/pc_bridge.py \\
        --vr  <voice_router root at 459e39d or later> \\
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


class Brain:
    """vr.live's engine, driven by board events. One problem at a time."""

    def __init__(self, a):
        sys.path.insert(0, str(Path(a.vr).expanduser()))
        from vr import form, live
        if not hasattr(form, "pick"):
            sys.exit("--vr is older than 459e39d (no vr.form.pick, and it can hang on fractures/sprains/wounds)")
        self.form, self.live = form, live
        self.trees = live.load_all(a.trees, a.slots)
        print(f"{len(self.trees)} trees with blanks loaded", flush=True)
        self.speech = None if a.typed else live.Speech(a.fasr, a.run)
        self.reject = a.reject
        self.cands, self.multi, self.turn, self.seq, self.heard = None, False, 0, 0, ""

    # ---- screens ----
    def _ref(self, name, card):
        t, _ = self.live._title(self.trees, name, card)
        return {"title": t, "node": f"{name}/{card}"}

    def show(self, kind, **kw):
        self.seq += 1
        d = {"v": 1, "seq": self.seq, "kind": kind, "heard": self.heard[:100]}
        d.update(kw)
        return d

    def form_screen(self, msg=""):
        fields = []
        for f in self.form.open_fields(self.cands):
            ch = [{"label": str(c.get("label", c.get("value")))[:46], "value": str(c["value"])[:46]}
                  for c in f.choices if c.get("value")]
            if ch:
                fields.append({"key": f.key, "q": f.question[:94], "choices": ch[:12],
                               "after": list(f.when) if f.when else None})
        top = [self._ref(n, c) for n, c in self.form.ranked_cards(self.cands, 3)]
        return self.show("form", fields=fields[:6], top=top, msg=msg)

    def card_screen(self, name, card):
        t, cites = self.live._title(self.trees, name, card)
        self.cands, self.turn = None, 0
        return self.show("card", card={"title": t, "cites": [str(c.get("ref", c)) if isinstance(c, dict) else str(c) for c in cites][:6], "node": f"{name}/{card}"})

    def result(self, kind, name, card):
        if kind == "open":
            return self.card_screen(name, card)
        if kind == "skip":
            return self.show("skip", cards=[self._ref(n, c) for n, c in card[:6]])
        if kind == "tabs":   # the bench board has no window keys: show the first problem's form
            self.cands = self.form.split_tabs(self.cands)[0]
            return self.form_screen("Several problems: showing the first")
        return self.form_screen()

    # ---- events ----
    def said(self, hyps: list[str], reject: int, rec: dict):
        self.heard = hyps[0] if hyps else ""
        rec.update(hyps=hyps, reject=reject)
        if self.speech and reject >= self.reject:
            return self.show("again", msg=f"Didn't catch that (reject {reject}). Hold TALK and say it again.")
        words = self.heard.strip().lower()
        if words in ("new", "home", "back") or not words:
            self.cands, self.turn = None, 0
            return self.show("start")
        if words == "multi":
            self.multi = True
            return self.show("start", msg="Next sentence = several problems")
        self.turn += 1
        if self.cands is None:
            self.cands = self.live.keep_trees(hyps, self.trees)
            rec["kept"] = [(c.name, round(c.p, 2)) for c in self.cands]
            if not self.cands:
                self.cands, self.turn = None, 0
                return self.show("start", msg="No blanks heard in any tree. Hold TALK and say what happened.")
            r = self.form.first_sentence(hyps, self.cands, multi=self.multi)
            self.multi = False
        else:
            r = self.form.answer(hyps[0], self.cands)
        return self.result(*r)

    def picked(self, p: dict):
        act = p.get("action")
        if act == "new":
            self.cands, self.turn, self.heard = None, 0, ""
            return self.show("start")
        if act == "open" and p.get("node"):
            name, _, card = p["node"].partition("/")
            return self.card_screen(name, card)
        if self.cands is None:
            return self.show("start", msg="Hold TALK and say what happened.")
        if act == "skip":
            return self.result("skip", None, self.form.skip(self.cands))
        if p.get("field"):
            return self.result(*self.form.pick(p["field"], p["value"], self.cands))
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pc_bridge")
    ap.add_argument("--vr", type=Path, required=True, help="voice_router folder (holds vr/), 459e39d or later")
    ap.add_argument("--trees", type=Path, nargs="+", required=True)
    ap.add_argument("--slots", type=Path, required=True)
    ap.add_argument("--fasr", type=Path, help="speech package root (holds fasr/)")
    ap.add_argument("--run", default="firstaid")
    ap.add_argument("--reject", type=int, default=70)
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--out", type=Path, default=Path(os.environ.get("WORK", "/mnt/data/first-aid-gpu")) / "speech" / "tether")
    ap.add_argument("--typed", action="store_true", help="type sentences instead of speaking")
    a = ap.parse_args(argv)
    if not a.typed and not a.fasr:
        ap.error("--fasr is needed unless --typed")

    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    out = a.out / stamp
    (out / "clips").mkdir(parents=True, exist_ok=True)
    (out / "screens").mkdir(exist_ok=True)
    log = open(out / "log.jsonl", "a")
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

    if a.typed:
        def typing():
            print("type a sentence (\"new\" = start over):", flush=True)
            for ln in sys.stdin:
                rec = {"typed": ln.strip()}
                emit(brain.said([ln.strip()], 0, rec), rec)
        threading.Thread(target=typing, daemon=True).start()

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
            rec = {"clip": str(wav), "take": take}
            if brain.speech:
                hyps, reject = brain.speech(wav)
                print(f"  heard: {hyps[0]!r} (reject {reject})", flush=True)
                emit(brain.said(hyps, reject, rec), rec)
            take = None
        elif ln.startswith("PICK "):
            p = json.loads(ln[5:])
            print(f"  PICK {p}", flush=True)
            rec = {"pick": p}
            emit(brain.picked(p), rec)
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
