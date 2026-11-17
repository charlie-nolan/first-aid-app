"""Figures for docs/VOICE_INTAKE.md: the voice flow diagram and 2.13" e-ink MOCKUPS of the new
screens (250x122, drawn 2x). Mockup wording is illustrative navigation text written for the
figure, not manual text. Re-run: python docs/voice/make_voice_figures.py"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent
W, H, S = 250, 122, 2
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def f(size, bold=False):
    return ImageFont.truetype(BOLD if bold else SANS, size * S)


def screen(title, right, rows, footer):
    im = Image.new("1", (W * S, H * S), 1)
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, W * S - 1, H * S - 1), outline=0, width=S)
    d.text((4 * S, 2 * S), title, font=f(9, True), fill=0)
    d.text((W * S - d.textlength(right, font=f(9)) - 4 * S, 2 * S), right, font=f(9), fill=0)
    d.line((0, 15 * S, W * S, 15 * S), fill=0, width=S)
    d.line((0, (H - 15) * S, W * S, (H - 15) * S), fill=0, width=S)
    d.text((4 * S, (H - 13) * S), footer, font=f(8), fill=0)
    y = 18
    for kind, a, b in rows:
        if kind == "field":                       # question + answer chips
            d.text((4 * S, y * S), a, font=f(8, True), fill=0)
            x = W - 4
            for chip in reversed(b):
                w = d.textlength(chip, font=f(8)) / S + 8
                x -= w
                d.rectangle((x * S, (y - 1) * S, (x + w - 2) * S, (y + 11) * S), outline=0, width=S)
                d.text(((x + 3) * S, y * S), chip, font=f(8), fill=0)
                x -= 3
            y += 15
        elif kind == "filled":
            d.text((4 * S, y * S), a, font=f(8), fill=0)
            d.text(((W - 4) * S - d.textlength(b, font=f(8, True)), y * S), b, font=f(8, True), fill=0)
            y += 13
        elif kind == "item":
            d.rectangle((4 * S, y * S, 15 * S, (y + 11) * S), fill=0)
            d.text((7 * S, y * S), a, font=f(8, True), fill=1)
            d.text((20 * S, y * S), b, font=f(9), fill=0)
            y += 15
        elif kind == "text":
            d.text((4 * S, y * S), a, font=f(9 if not b else 8, bool(b)), fill=0)
            y += 13
    return im


def tabs_bar(im, labels, active):
    d = ImageDraw.Draw(im)
    x = 4
    for i, lab in enumerate(labels):
        w = d.textlength(f"{i + 1} {lab}", font=f(8)) / S + 8
        box = (x * S, 1 * S, (x + w) * S, 14 * S)
        if i == active:
            d.rectangle(box, fill=0)
            d.text(((x + 4) * S, 2 * S), f"{i + 1} {lab}", font=f(8, True), fill=1)
        else:
            d.rectangle(box, outline=0, width=S)
            d.text(((x + 4) * S, 2 * S), f"{i + 1} {lab}", font=f(8), fill=0)
        x += w + 3
    return im


def montage(panels, name):
    pad, cap = 16, 22
    pw, ph = W * S, H * S
    cols = 2
    rows = (len(panels) + 1) // 2
    out = Image.new("RGB", (cols * (pw + pad) + pad, rows * (ph + cap + pad) + pad), "#e1e3df")
    d = ImageDraw.Draw(out)
    for i, (caption, im) in enumerate(panels):
        x = pad + (i % cols) * (pw + pad)
        y = pad + (i // cols) * (ph + cap + pad)
        d.text((x, y), caption, font=ImageFont.truetype(SANS, 15), fill="#333")
        out.paste(im.convert("RGB"), (x, y + cap))
    out.save(OUT / name)


def flow():
    steps = [("1  Say it", "hold TALK\n(TALK+WIN_R:\nseveral problems)"),
             ("2  Recognizer", "5 best word\nguesses, core\nword list"),
             ("3  Router (MoE)", "keeps 2-3 likely\ntrees + sectors;\nsector re-decode"),
             ("4  Life rules", "not breathing,\nheavy bleeding...\n-> card NOW"),
             ("5  Keyword fill", "every likely tree's\nblanks at once;\nshared blanks once"),
             ("6  One form", "only missing blanks,\nmost telling first;\nmadlibs snap"),
             ("7  Cited card", "or SKIP: likely\ncards, best first")]
    bw, bh, gap = 190, 128, 34
    im = Image.new("RGB", (len(steps) * (bw + gap) + gap, bh + 120), "white")
    d = ImageDraw.Draw(im)
    t, b = ImageFont.truetype(BOLD, 17), ImageFont.truetype(SANS, 15)
    d.text((gap, 14), "Voice intake (2026-11-17): first sentence fills the blanks, one screen asks the rest",
           font=ImageFont.truetype(BOLD, 19), fill="#111")
    for i, (h, body) in enumerate(steps):
        x, y = gap + i * (bw + gap), 60
        fill = "#fde2e1" if i == 3 else "#e8f1fb" if i in (2, 4, 5) else "#f2f2f2"
        d.rounded_rectangle((x, y, x + bw, y + bh), 12, fill=fill, outline="#555", width=2)
        d.text((x + 12, y + 10), h, font=t, fill="#111")
        d.multiline_text((x + 12, y + 40), body, font=b, fill="#333", spacing=4)
        if i < len(steps) - 1:
            ay = y + bh // 2
            d.line((x + bw + 4, ay, x + bw + gap - 6, ay), fill="#555", width=3)
            d.polygon([(x + bw + gap - 6, ay - 6), (x + bw + gap - 6, ay + 6), (x + bw + gap, ay)], fill="#555")
    d.text((gap, bh + 80), "Buttons-only mode is unchanged: the same trees, page by page. "
           "Nothing is generated on the device; every card is a cited manual card.",
           font=b, fill="#555")
    im.save(OUT / "voice-flow.png")


if __name__ == "__main__":
    flow()
    form = screen("Ankle hurt - 3 left", "MOCKUP", [
        ("filled", "heard: fell, ankle, swelling", "ankle"),
        ("field", "Weight on it?", ["yes", "no"]),
        ("field", "Bone showing / bent?", ["yes", "no"]),
        ("field", "Toes numb or cold?", ["yes", "no"]),
        ("field", "How long ago?", ["now", "hours", "days"])],
        "Say the answers  -  UP+DOWN: skip")
    after = screen("Ankle hurt - 1 left", "MOCKUP", [
        ("filled", "weight on it", "no"),
        ("filled", "bone showing / bent", "no"),
        ("filled", "toes numb or cold", "no"),
        ("field", "How long ago?", ["now", "hours", "days"])],
        "\"no weight, no bone, toes fine\" filled 3")
    skip = screen("Likely cards", "1/3", [
        ("item", "1", "Possible fracture: splint it"),
        ("item", "2", "Sprain: rest, ice, wrap"),
        ("item", "3", "When to evacuate")],
        "Press or say a number  -  BACK: form")
    tabs = tabs_bar(screen("", "", [
        ("filled", "heard: cut, leg, bleeding", "leg"),
        ("field", "Still bleeding after pressure?", ["yes", "no"]),
        ("field", "Something stuck in it?", ["yes", "no"])],
        "WIN L/R: switch problem  -  UP+DOWN: skip"), ["Wound", "Fracture", "Cold"], 0)
    montage([("1  After the first sentence: one form, only the missing blanks", form),
             ("2  One spoken answer fills three blanks", after),
             ("3  SKIP: cards still possible, best first", skip),
             ("4  TALK+WIN_R: one tab per problem (up to 5)", tabs)], "voice-screens-mockup.png")
    print("wrote", OUT / "voice-flow.png", OUT / "voice-screens-mockup.png")
