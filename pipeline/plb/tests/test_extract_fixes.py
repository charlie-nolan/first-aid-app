"""Extractor fixes found on the real manuals, each on a small generated PDF.

Placeholder text only, like test_pipeline.py: these test layout handling, not content.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from plb.extract import decode_cids, extract, normalize
from plb.sources import verify

W, H = letter


def run(tmp_path: Path, draw, numbering="dash", figures=True):
    pdf = tmp_path / "t.pdf"
    c = canvas.Canvas(str(pdf), pagesize=letter)
    draw(c)
    c.showPage()
    c.save()
    out = tmp_path / "out"
    extract(pdf, "T", out, figures=figures, numbering=numbering)
    secs = {s["para"]: s for s in json.loads((out / "sections.json").read_text())}
    figs = json.loads((out / "figures.json").read_text())
    audit = json.loads((out / "notices_audit.json").read_text())
    return secs, figs, audit


def lines(c, y, rows, font="Helvetica", size=10):
    c.setFont(font, size)
    for r in rows:
        c.drawString(72, y, r)
        y -= 14
    return y


def test_small_caps_heading_keeps_initials(tmp_path):
    def draw(c):
        x, y = 72, H - 120
        for big, rest in [("A", "PPLICATION OF "), ("T", "OURNIQUETS")]:
            c.setFont("Helvetica-Bold", 14); c.drawString(x, y, big)
            x += stringWidth(big, "Helvetica-Bold", 14)
            c.setFont("Helvetica-Bold", 11); c.drawString(x, y, rest)
            x += stringWidth(rest, "Helvetica-Bold", 11)
        lines(c, y - 20, ["1-1. Placeholder paragraph under a small-caps heading."])
    secs, _, _ = run(tmp_path, draw)
    assert secs["1-1"]["heading"] == "APPLICATION OF TOURNIQUETS"


def test_page_label_is_not_a_heading(tmp_path):
    def draw(c):
        y = lines(c, H - 120, ["REAL HEADING"], "Helvetica-Bold", 11)
        y = lines(c, y, ["1-1. First placeholder paragraph."])
        y = lines(c, y, ["209B"], "Helvetica-Bold", 11)
        lines(c, y, ["1-2. Second placeholder paragraph."])
    secs, _, _ = run(tmp_path, draw)
    assert secs["1-2"]["heading"] == "REAL HEADING"


def test_question_mark_bullets(tmp_path):
    def draw(c):
        lines(c, H - 120, ["1-1. There are three placeholder items, they are--",
                           "? Alpha item.", "? Bravo item.", "? Charlie item."])
    secs, _, _ = run(tmp_path, draw)
    assert secs["1-1"]["text"].endswith("--\n- Alpha item.\n- Bravo item.\n- Charlie item.")


def test_cid_page_decoded():
    enc = lambda s: "".join(f"(cid:{ord(ch) - 29})" if ch != " " else " " for ch in s)
    page = [enc("the water and the fire are in the shelter for the survival of the group")] * 3
    assert decode_cids(page)[0].startswith("the water and the fire")
    assert decode_cids(["(cid:3) one bullet only"]) == ["(cid:3) one bullet only"]


def test_decimal_numbering(tmp_path):
    def draw(c):
        lines(c, H - 120, ["4.7.3. Overcoming placeholder fear text.", "continues here.",
                           "4.7.4. Next placeholder paragraph."])
    secs, _, _ = run(tmp_path, draw, numbering="decimal")
    assert set(secs) == {"4.7.3", "4.7.4"}
    assert secs["4.7.3"]["text"] == "Overcoming placeholder fear text. continues here."


def test_heading_numbering(tmp_path):
    def draw(c):
        y = lines(c, H - 120, ["PHYSICAL PLACEHOLDER"], "Helvetica-Bold", 11)
        y = lines(c, y, ["Body text for the first section", "goes on here"])
        y = lines(c, y, ["SECOND PLACEHOLDER"], "Helvetica-Bold", 11)
        lines(c, y, ["Body text for the second section"])
    secs, _, _ = run(tmp_path, draw, numbering="headings")
    assert [(s["heading"], s["text"]) for s in secs.values()] == [
        ("PHYSICAL PLACEHOLDER", "Body text for the first section goes on here"),
        ("SECOND PLACEHOLDER", "Body text for the second section")]
    assert list(secs) == ["1.1", "1.2"]


def test_notice_boxes_kept_verbatim(tmp_path):
    def draw(c):
        y = lines(c, H - 120, ["PLACEHOLDER TOPIC"], "Helvetica-Bold", 11)
        y = lines(c, y, ["1-1. Alpha placeholder paragraph."]) - 10
        y = lines(c, y, ["CAUTION"], "Helvetica-Bold", 10)
        y = lines(c, y, ["Do not do the placeholder thing."]) - 10
        y = lines(c, y, ["1-2. Bravo placeholder paragraph."]) - 10
        y = lines(c, y, ["WARNING"], "Helvetica-Bold", 10)
        y = lines(c, y, ["The placeholder must be loosened if the part distal to the",
                         "placeholder becomes cool or numb."]) - 10
        lines(c, y, ["1-3. Charlie placeholder paragraph."])
    secs, _, audit = run(tmp_path, draw)
    assert secs["1-1"]["notices"] == [{"kind": "CAUTION", "text": "Do not do the placeholder thing."}]
    assert secs["1-2"]["notices"] == [{"kind": "WARNING", "text":
        "The placeholder must be loosened if the part distal to the placeholder becomes cool or numb."}]
    assert secs["1-2"]["text"] == "Bravo placeholder paragraph."
    assert all(s["heading"] == "PLACEHOLDER TOPIC" for s in secs.values())
    assert audit["missing"] == 0 and audit["notices_kept"] == 2


def test_fractions():
    assert normalize("fold to 1½ to 2 inches") == "fold to 1 1/2 to 2 inches"
    assert normalize("1 1⁄2 inches") == "1 1/2 inches"
    assert normalize("½ inch") == "1/2 inch"


def test_figure_captions(tmp_path):
    def draw(c):
        y = lines(c, H - 100, ["Figure 1-1. Placeholder one ........ 3",
                               "Figure 1-2. Placeholder two 4"]) - 20
        c.rect(80, y - 100, 200, 90); c.rect(330, y - 100, 200, 90)
        y -= 115
        c.setFont("Helvetica", 10)
        c.drawString(100, y, "Figure 1-1. Left placeholder")
        c.drawString(350, y, "Figure 1-2. Right placeholder")
        c.rect(150, y - 120, 300, 90)
        c.drawString(100, y - 135, "Figure 1-4. Lower placeholder")
    _, figs, audit = run(tmp_path, draw)
    assert [(f["num"], f["title"].strip()) for f in figs] == [
        ("1-1", "Left placeholder"), ("1-2", "Right placeholder"), ("1-4", "Lower placeholder")]
    assert figs[0]["bbox"][2] < W / 2 < figs[1]["bbox"][0]
    assert audit["figure_gaps"] == ["1-3"]


def test_us_gov_work_distribution_allowed(tmp_path):
    (tmp_path / "s").mkdir()
    pdf = tmp_path / "s" / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4 placeholder")
    import hashlib
    man = tmp_path / "sources.yaml"
    man.write_text(yaml.safe_dump({"manuals": [{
        "id": "A", "title": "A", "edition": "1", "file": "s/a.pdf",
        "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        "distribution": "US Government work (17 USC 105), no statement printed"}]}))
    assert verify(tmp_path, man) == []
