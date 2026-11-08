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
                               "Figure 1-2. Placeholder two 4",
                               "Figure 1-4. Placeholder four 5"]) - 20
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


def test_caption_without_period_and_trailing_number(tmp_path):
    def draw(c):
        c.rect(150, H - 200, 300, 90)
        c.setFont("Helvetica", 10)
        c.drawString(72, H - 215, "Figure 6-7 Snow placeholder")
        c.rect(150, H - 350, 300, 90)
        c.drawString(72, H - 365, "Figure 6-8. Minutes until placeholder in the most susceptible 5")
        c.drawString(72, H - 395, "Figure 6-9 shows the placeholder in body text.")
    _, figs, _ = run(tmp_path, draw)
    assert [(f["num"], f["title"].strip()) for f in figs] == [
        ("6-7", "Snow placeholder"),
        ("6-8", "Minutes until placeholder in the most susceptible 5")]


def test_two_column_page_reads_left_then_right(tmp_path):
    def draw(c):
        c.setFont("Helvetica", 10)
        y = H - 100
        for i in range(12):
            c.drawString(60, y, f"Left column placeholder line {i} goes here")
            c.drawString(330, y, f"Right column placeholder line {i} here")
            y -= 14
    secs, _, _ = run(tmp_path, draw, numbering="headings")
    # headings mode with no heading keeps nothing; read the lines directly instead
    from plb.extract import read_lines
    texts = [l.text for _, ls in read_lines(tmp_path / "t.pdf") for l in ls]
    assert texts[0].startswith("Left column placeholder line 0")
    assert texts[11].startswith("Left column placeholder line 11")
    assert texts[12].startswith("Right column placeholder line 0")


def test_audit_lists_inline_notes(tmp_path):
    def draw(c):
        lines(c, H - 120, ["1-1. Alpha placeholder sentence. NOTE: Inline placeholder note",
                           "continues in the paragraph.",
                           "1-2. Bravo placeholder about a WARNING ORDER."])
    secs, _, audit = run(tmp_path, draw)
    assert "NOTE: Inline placeholder note" in secs["1-1"]["text"]
    assert len(audit["inline_notes"]) == 1 and audit["inline_notes"][0].startswith("T/1-1")
    assert len(audit["keyword_elsewhere"]) == 1 and "WARNING ORDER" in audit["keyword_elsewhere"][0]


def test_numbered_table_note_is_a_notice(tmp_path):
    def draw(c):
        y = lines(c, H - 120, ["1-1. Alpha placeholder paragraph before a table."]) - 20
        lines(c, y, ["5. CAUTION: Hourly placeholder should not exceed 1 1/2 units.",
                     "6. CAUTION: Daily placeholder should not exceed 12 units."])
    secs, _, audit = run(tmp_path, draw)
    assert [n["text"] for n in secs["1-1"]["notices"]] == [
        "Hourly placeholder should not exceed 1 1/2 units.",
        "Daily placeholder should not exceed 12 units."]
    assert audit["missing"] == 0


def test_unnumbered_stretch_in_numbered_manual(tmp_path):
    def draw(c):
        y = lines(c, H - 100, ["Chapter 2"], "Helvetica-Bold", 14) - 6
        y = lines(c, y, ["PLACEHOLDER MEDICINE"], "Helvetica-Bold", 11)
        y = lines(c, y, ["Unnumbered placeholder text that opens the chapter", "and carries on here."])
        y = lines(c, y, ["SECOND PLACEHOLDER HEADING"], "Helvetica-Bold", 11)
        y = lines(c, y, ["More unnumbered placeholder text."])
        lines(c, y, ["2-1. First numbered placeholder paragraph."])
    secs, _, _ = run(tmp_path, draw)
    assert secs["2.h1"]["heading"] == "PLACEHOLDER MEDICINE"
    assert secs["2.h1"]["text"] == "Unnumbered placeholder text that opens the chapter and carries on here."
    assert secs["2.h2"]["text"] == "More unnumbered placeholder text."
    assert secs["2-1"]["text"] == "First numbered placeholder paragraph."
    assert secs["2-1"]["heading"] == "SECOND PLACEHOLDER HEADING"


def test_words_without_a_space_character_are_split(tmp_path):
    # Some PDFs (ATP 3-50.21) position words with no space glyph between them; a gap of
    # about 2 pt at 10 pt used to be merged ("personnelmust").
    def draw(c):
        c.setFont("Helvetica", 10)
        x = 72
        for w in ["1-1.", "Isolated", "personnel", "must", "understand."]:
            c.drawString(x, H - 120, w)
            x += stringWidth(w, "Helvetica", 10) + 2.0
    secs, _, _ = run(tmp_path, draw)
    assert secs["1-1"]["text"] == "Isolated personnel must understand."


def test_join_words_gap_scales_with_size():
    from plb.extract import join_words
    w = lambda t, x0, x1, size: {"text": t, "x0": x0, "x1": x1, "size": size}
    assert join_words([w("A", 0, 8.66, 12), w("PPLICATION", 8.64, 60, 9.5)]) == "APPLICATION"
    assert join_words([w("Direct", 0, 24.85, 10), w("pressure.", 25.68, 61, 10)]) == "Direct pressure."
    assert join_words([w("big", 0, 30, 40), w("type", 30.8, 70, 40)]) == "bigtype"     # 0.8 pt at 40 pt


def test_degree_lookalikes_map_like_degree_sign():
    from plb.extract import normalize
    out = [" ".join(normalize(t).split()) for t in ("95° F", "95º F (37º C)", "40˚F")]
    assert out == ["95 degrees F", "95 degrees F (37 degrees C)", "40 degrees F"]


def test_ruled_table_rows_read_cell_by_cell(tmp_path):
    """CERT Table 3.2: a ruled 3-column table whose last row continues on the next page.
    Read line by line the columns interleave; with table_pages each row is one section."""
    pdf = tmp_path / "t.pdf"
    c = canvas.Canvas(str(pdf), pagesize=letter)
    xs = [80, 200, 360, 530]

    def table(y, rows, header=True):
        hdr = [["Kind", "Layers", "Signs"]] if header else []
        allrows = hdr + rows
        heights = [16 * max(1, max(len(cell) for cell in r)) for r in allrows]
        top = y
        c.setFont("Helvetica", 10)
        for r, h in zip(allrows, heights):
            for k, cell in enumerate(r):
                for j, item in enumerate(cell if isinstance(cell, list) else [cell]):
                    c.drawString(xs[k] + 4, top - 12 - 14 * j, item)
            c.line(xs[0], top, xs[-1], top)
            top -= h
        c.line(xs[0], top, xs[-1], top)
        for x in xs:
            c.line(x, y, x, top)
        return top

    c.setFont("Helvetica-Bold", 11); c.drawString(72, H - 90, "PLACEHOLDER CLASSES")
    c.setFont("Helvetica", 10); c.drawString(72, H - 108, "Some placeholder text before the table.")
    c.setFont("Helvetica-Bold", 9); c.drawString(72, H - 130, "Table 3.2: Placeholder Chart")   # bold, as in CERT
    table(H - 140, [[["Mild"], ["• Outer layer"], ["• Red skin", "• Pain"]],
                    [["Deep"], ["• Outer layer", "• Middle layer"], ["• Dry", "• White"]]])
    c.showPage()
    bottom = table(H - 80, [[[""], ["• Some deeper damage"], ["• Little pain"]]])
    c.setFont("Helvetica-Bold", 11); c.drawString(72, bottom - 30, "AFTER THE TABLE")
    c.setFont("Helvetica", 10); c.drawString(72, bottom - 48, "Placeholder text after the table.")
    c.showPage(); c.save()

    out = tmp_path / "out"
    extract(pdf, "T", out, figures=False, numbering="headings", table_pages=(1, 2))
    secs = {s["para"]: s for s in json.loads((out / "sections.json").read_text())}
    assert secs["1.t1"]["text"] == "Mild: Layers: Outer layer. Signs: Red skin; Pain."
    assert secs["1.t2"]["text"] == ("Deep: Layers: Outer layer; Middle layer; Some deeper damage. "
                                    "Signs: Dry; White; Little pain.")
    assert secs["1.t2"]["pages"] == [1, 2] and secs["1.t1"]["heading"] == "Table 3.2: Placeholder Chart"
    texts = " ".join(s["text"] for s in secs.values())
    assert "Placeholder text after the table." in texts and "Kind Layers Signs" not in texts
