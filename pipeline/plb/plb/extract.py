"""PDF -> lines -> numbered sections + figure crops.

Army and Air Force doctrine numbers its paragraphs ("2-15. Text..."), and those
numbers are what every tree node cites. This module turns a manual into
`sections.json` (one record per numbered paragraph, verbatim text) and
`figures.json` + PNG crops (one per "Figure X-Y." caption).

The figure step is pluggable: `--figure-cmd` runs an external extractor (e.g. the
cyberdeck project's) that must write the same figures.json format.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

PARA_RE = re.compile(r"^(?P<num>(?:[A-Z]|\d{1,2})-\d{1,3})\.\s+(?P<rest>.*)$")
# Air Force handbooks number paragraphs "4.7.3." instead (numbering: decimal in sources.yaml)
DECIMAL_RE = re.compile(r"^(?P<num>\d{1,2}(?:\.\d{1,3}){1,4})\.\s+(?P<rest>.*)$")
# CAUTION / WARNING / NOTE / DANGER boxes: the keyword alone on a line, or "NOTE: text"
# Table notes number them: "5. CAUTION: Hourly fluid intake should not exceed 1 1/2 qt." (TB MED 507)
NOTICE_RE = re.compile(r"^(?:\d{1,2}\.\s+(?=(?:CAUTION|WARNING|NOTE|DANGER)S?:))?"
                       r"(?P<kind>CAUTION|WARNING|NOTE|DANGER)S?\b\s*[:.]?\s*(?P<rest>.*)$")
NOTICE_WORD_RE = re.compile(r"\b(CAUTION|WARNING|NOTE|DANGER)S?\b")
# "... tissue lost. NOTE: Use of a band ..." or "(NOTE: ...)": a note inside the paragraph's
# own sentences, not a box; it stays in the text and the audit lists it for review
INLINE_NOTE_RE = re.compile(r"\(?(CAUTION|WARNING|NOTE|DANGER):")
FIG_RE = re.compile(r"^Figure\s+(?P<num>(?:[A-Z]|\d{1,2})-\d{1,3})(?:[.\-]\s*|\s+(?=[A-Z]))"
                    r"(?P<title>.*)$")
FIG_SPLIT_RE = re.compile(r"Figure\s+(?P<num>(?:[A-Z]|\d{1,2})-\d{1,3})(?:[.\-]\s*|\s+(?=[A-Z]))")
# list-of-figures entries: "Windlass and Ratchet Tourniquets ........ 48", or a page of
# captions that all end in page numbers
TOC_LEADER_RE = re.compile(r"(\.\s*){3,}")
TOC_PAGENO_RE = re.compile(r"\s\d{1,3}$")
TABLE_RE = re.compile(r"^Table\s+(?P<num>(?:[A-Z]|\d{1,2})-\d{1,3})\.\s*(?P<title>.*)$")
# "Chapter 4", "CHAPTER 4. TITLE", "Appendix A Title" start a chapter. Cross-references
# in running text ("chapter 6, see", "Chapter 1 describes", "Appendix C).") must not.
CHAPTER_RE = re.compile(r"^(?:Chapter|CHAPTER|Appendix|APPENDIX)\s+(?:\d+|[A-Z])"
                        r"(?=$|[.:\-]?\s+(?:[A-Z(]|$)|[.:]$)")

# Some DoD PDFs use font subsets whose codes are shifted (e.g. "6HSWHPEHU" = "September").
COMMON = set("""the of and to a in is be or for with it as that by on are from this at can
an not if you your water fire food survival first aid casualty bleeding wound use do
will should may when has have into which all one two any other them than then they""".split())

LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl",
             "‘": "'", "’": "'", "“": '"', "”": '"', "–": "-",
             "—": "--", "•": "*", "©": "(C)", " ": " ", "°": " degrees ",
             "º": " degrees ", "˚": " degrees ",   # CERT prints 95º F; NFKD would give "o"
             "⁄": "/", "∕": "/"}
FRACTIONS = {"½": "1/2", "¼": "1/4", "¾": "3/4", "⅓": "1/3", "⅔": "2/3", "⅛": "1/8"}


def normalize(text: str) -> str:
    """Map to the plain ASCII the e-ink font can draw. Anything unmappable becomes '?'."""
    for k, v in FRACTIONS.items():                      # NFKD would give "1?2"
        text = re.sub(rf"(\d){k}", rf"\1 {v}", text).replace(k, v)
    for k, v in LIGATURES.items():
        text = text.replace(k, v)
    text = unicodedata.normalize("NFKD", text)
    return "".join(c if 32 <= ord(c) < 127 or c == "\n" else ("" if unicodedata.combining(c) else "?")
                   for c in text)


def english_ratio(s: str) -> float:
    words = re.findall(r"[a-z]+", s.lower())
    return sum(w in COMMON for w in words) / max(1, len(words))


def unshift(s: str, k: int) -> str:
    return "".join(chr(ord(c) + k) if 33 <= ord(c) + k < 127 and c != " " else c for c in s)


CID_RE = re.compile(r"\(cid:(\d+)\)")


def _cid_sub(t: str, k: int) -> str:
    return CID_RE.sub(lambda m: chr(int(m[1]) + k) if 32 <= int(m[1]) + k < 127 else "", t)


def decode_cids(texts: list[str]) -> list[str]:
    """Some PDFs (e.g. ATP 3-90.98) have no unicode map at all, so every glyph comes out
    as "(cid:N)". When most of a page is like that, N is a fixed offset from ASCII; pick the
    offset that reads as English. Pages with only the odd bullet glyph are left alone."""
    blob = " ".join(texts)
    if len(CID_RE.findall(blob)) < 40 or len(CID_RE.sub("", blob).strip()) > len(blob) * 0.2:
        return texts
    best, best_k = 0.0, None
    for k in (29, 31, 0, 3, -3):
        r = english_ratio(_cid_sub(blob, k))
        if r > best:
            best, best_k = r, k
    if best_k is None or best < 0.1:
        return texts
    return [_cid_sub(t, best_k) for t in texts]


def page_shift(lines: list[str]) -> int:
    """Detect a shifted font encoding for a whole page (never per line: short lines give
    false hits). Returns the shift to apply, or 0."""
    blob = " ".join(lines)
    if len(re.findall(r"[A-Za-z]+", blob)) < 40 or english_ratio(blob) > 0.05:
        return 0
    for k in (29, 3, -3):
        if english_ratio(unshift(blob, k)) > 0.2:
            return k
    return 0


@dataclass
class Line:
    page: int
    top: float
    bottom: float
    x0: float
    text: str
    bold: bool
    size: float


@dataclass
class Section:
    id: str            # "ATP3-50.21/2-15"
    manual: str
    para: str          # "2-15"
    chapter: str
    heading: str
    pages: list[int] = field(default_factory=list)
    text: str = ""
    notices: list[dict] = field(default_factory=list)   # [{"kind": "CAUTION", "text": ...}]


@dataclass
class Figure:
    id: str            # "ATP3-50.21/fig/2-3"
    manual: str
    num: str
    title: str
    page: int
    bbox: list[float]
    png: str
    restricted: bool = False   # caption says copyrighted / used with permission
    dpi: int = 200


def split_columns(words: list[dict], width: float) -> list[list[dict]]:
    """Two-column pages (Poisonous Snakes of the World) would otherwise interleave the
    columns line by line. Call it two columns when almost no word crosses the middle and
    both halves hold real text."""
    mid = width / 2
    if len(words) < 60:
        return [words]
    cross = sum(w["x0"] < mid < w["x1"] for w in words)
    left = [w for w in words if w["x1"] <= mid]
    right = [w for w in words if w["x0"] >= mid]
    if cross > len(words) * 0.02 or min(len(left), len(right)) < len(words) * 0.3:
        return [words]
    rest = [w for w in words if w["x0"] < mid < w["x1"]]
    return [left + rest, right]


def group_rows(words: list[dict]) -> list[list[dict]]:
    """Group words into lines by vertical overlap, so a large small-caps initial ("A" of
    "APPLICATION") lands on the same line as the rest of its word."""
    rows: list[list[dict]] = []
    bounds: list[list[float]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        hw = w["bottom"] - w["top"]
        for r, b in zip(rows, bounds):
            ov = min(b[1], w["bottom"]) - max(b[0], w["top"])
            if ov > 0.5 * min(hw, b[1] - b[0]):
                r.append(w)
                b[0], b[1] = min(b[0], w["top"]), max(b[1], w["bottom"])
                break
        else:
            rows.append([w])
            bounds.append([w["top"], w["bottom"]])
    for r in rows:
        r.sort(key=lambda w: w["x0"])
    return sorted(rows, key=lambda r: min(w["top"] for w in r))


JOIN_GAP = 0.03       # x font size: closer than this is one word (0.3 pt at 10 pt)


def join_words(ws: list[dict]) -> str:
    """Words that touch (gap under 3% of the font size) are one word split by a font change
    ("A" + "PPLICATION"). Real spaces can be as narrow as 0.8 pt at 10 pt (TC 4-02.1)."""
    out = ""
    for i, w in enumerate(ws):
        size = min(w.get("size", 10.0), ws[i - 1].get("size", 10.0)) if i else 0
        if i and w["x0"] - ws[i - 1]["x1"] >= JOIN_GAP * size:
            out += " "
        out += w["text"]
    return out


def read_lines(pdf_path: Path, margin_top: float = 0.07, margin_bottom: float = 0.07):
    """Yield (page_obj, [Line]) skipping running headers/footers."""
    import pdfplumber

    with pdfplumber.open(pdf_path) as doc:
        for pno, page in enumerate(doc.pages, start=1):
            h = page.height
            words = page.extract_words(extra_attrs=["fontname", "size"], keep_blank_chars=False,
                                       use_text_flow=True, x_tolerance_ratio=0.1)
            # size-1 words are invisible labels ("62B") left by the typesetter
            words = [w for w in words
                     if w["top"] >= h * margin_top and w["bottom"] <= h * (1 - margin_bottom)
                     and w["size"] >= 3]
            rows = [r for col in split_columns(words, page.width) for r in group_rows(col)]
            raw = decode_cids([join_words(ws) for ws in rows])
            lines = []
            for ws, text in zip(rows, raw):
                text = normalize(text)
                # glyphs with no unicode mapping come out as "(cid:N)" or a bare "?"; at line
                # start they are almost always bullets, elsewhere they are dropped
                text = re.sub(r"^(\(cid:\d+\)\s*|\?\s+)", "* ", text)
                text = re.sub(r"\(cid:\d+\)", "", text)
                bold = sum("bold" in w["fontname"].lower() for w in ws) > len(ws) / 2
                lines.append(Line(pno, min(w["top"] for w in ws), max(w["bottom"] for w in ws),
                                  ws[0]["x0"], text, bold, max(w["size"] for w in ws)))
            k = page_shift([l.text for l in lines])
            if k:
                for l in lines:
                    l.text = unshift(l.text, k)
            yield page, lines


SUBPARA_RE = re.compile(r"^([a-z]\.|\(\d{1,2}\)|\([a-z]\)|\* )\s*")
RESTRICTED_RE = re.compile(r"(\(C\) ?(19|20)\d\d|copyright|used with permission|courtesy of|reprinted)", re.I)


def join_text(parts: list[str]) -> str:
    """Join PDF lines into passage text. Sub-paragraphs ("a.", "(1)") and bullets start a
    new line ("\n"); bullets become "- ". Everything else is reflowed."""
    lines: list[str] = []
    for p in parts:
        p = re.sub(r"\s+", " ", p).strip()
        if not p:
            continue
        if p.startswith("* "):
            lines.append("- " + p[2:])
        elif SUBPARA_RE.match(p) or not lines:
            lines.append(p)
        elif lines[-1].endswith("-") and p[:1].islower():
            lines[-1] = lines[-1][:-1] + p          # re-join a hyphenated line break
        else:
            lines[-1] += " " + p
    return "\n".join(lines)


JUNK_HEADING_RE = re.compile(r"^[\dA-Z\-.]{1,8}$")   # page / figure labels such as "209B"


def chapter_key(chapter: str) -> str:
    """"Chapter 2" -> "2", "Appendix A" -> "A", no chapter yet -> "0"."""
    m = re.match(r"^(?:chapter|appendix)\s+(\d+|[A-Z])", chapter, re.I)
    return m[1] if m else "0"


def table_cell_items(cell: str | None) -> list[str]:
    """A table cell's bullet items, each one line ("• Complete destruction of epidermis\nand
    dermis" -> ["Complete destruction of epidermis and dermis"])."""
    text = normalize(CID_RE.sub("\u2022", cell or "")).replace("*", "\u2022")   # unmapped bullets too
    parts = [p for p in re.split(r"\u2022", text)] if "\u2022" in text else [text]
    return [re.sub(r"\s+", " ", p).strip() for p in parts if p.strip()]


def table_row_text(hdr: list[str], cells: list[list[str]]) -> str:
    """"Superficial: Skin Layers Affected: Epidermis. Signs: Reddened, dry skin; Pain; ..."
    The cell words are verbatim; only the column names and punctuation are added."""
    first = "; ".join(cells[0])
    rest = [f"{h}: {'; '.join(c)}." for h, c in zip(hdr[1:], cells[1:]) if c]
    return f"{first}: " + " ".join(rest) if first else " ".join(rest)


def is_heading(line: Line, body_size: float) -> bool:
    t = line.text.strip()
    if not t or len(t) > 80 or PARA_RE.match(t) or FIG_RE.match(t) or t.endswith("."):
        return False
    if NOTICE_RE.match(t) or (JUNK_HEADING_RE.match(t) and any(c.isdigit() for c in t)):
        return False
    return line.bold or line.size > body_size + 0.5 or (t.isupper() and len(t) > 3)


def extract(pdf_path: Path, manual_id: str, out_dir: Path, figures: bool = True,
            dpi: int = 200, numbering: str = "dash",
            skip_pages: tuple[int, ...] = (),
            table_pages: tuple[int, ...] | str = (),
            chapter_pattern: str | None = None) -> tuple[list[Section], list[Figure]]:
    """numbering: "dash" (Army "2-15."), "decimal" (Air Force "4.7.3.") or "headings"
    (no paragraph numbers: each heading starts a section, numbered "<page>.<n>").
    skip_pages: 1-based pages that are not part of the publication (a reseller's note).
    table_pages: 1-based pages whose ruled tables are read cell by cell (one section per row,
    "<page>.t<n>") instead of line by line, which interleaves the columns; "all" = every page.
    chapter_pattern: extra regex for chapter lines in books that don't say "Chapter"
    (Aerie: "Section One:")."""
    chap_re = re.compile(f"(?:{CHAPTER_RE.pattern})|(?:{chapter_pattern})") if chapter_pattern \
        else CHAPTER_RE
    out_dir.mkdir(parents=True, exist_ok=True)
    fig_dir = out_dir / "figures"
    para_re = DECIMAL_RE if numbering == "decimal" else PARA_RE
    sections: list[Section] = []
    figs: list[Figure] = []
    cur: Section | None = None
    buf: list[str] = []
    chapter, heading = "", ""
    notice: dict | None = None      # box being read: kind, parts, bottom, page
    pending: list[dict] = []        # boxes seen before the paragraph they belong to
    keyword_lines = 0               # CAUTION/WARNING/NOTE/DANGER words seen, for the audit
    per_page: dict[int, int] = {}
    per_chapter: dict[str, int] = {}    # unnumbered stretches inside a numbered manual
    table_title = ""                    # last "Table 3.2: ..." caption, the heading for its rows
    last_row: dict | None = None        # a table row that may continue on the next page
    per_table: dict[int, int] = {}      # table rows per page, "<page>.t<n>"

    def end_notice():
        nonlocal notice
        if notice is not None:
            rec = {"kind": notice["kind"], "text": join_text(notice["parts"])}
            (cur.notices if cur is not None else pending).append(rec)
            notice = None

    def flush():
        nonlocal cur, buf
        end_notice()
        if cur is not None:
            cur.text = join_text(buf)
            sections.append(cur)
        cur, buf = None, []

    def start(sec: Section, first: str):
        nonlocal cur, buf
        cur, buf = sec, [first] if first else []
        cur.notices.extend(pending)
        pending.clear()

    def emit_table(page, tbl):
        nonlocal last_row
        rows = tbl.extract()
        hdr = [normalize(c or "").strip() for c in rows[0]]
        for row in rows[1:]:
            cells = [table_cell_items(c) for c in row]
            if not cells[0] and last_row is not None and len(cells) == len(last_row["cells"]):
                # continues the row above
                for k, items in enumerate(cells[1:], 1):
                    last_row["cells"][k].extend(items)
                if page.page_number not in last_row["sec"].pages:
                    last_row["sec"].pages.append(page.page_number)
                last_row["sec"].text = table_row_text(last_row["hdr"], last_row["cells"])
                continue
            n = per_table[page.page_number] = per_table.get(page.page_number, 0) + 1
            num = f"{page.page_number}.t{n}"
            sec = Section(f"{manual_id}/{num}", manual_id, num, chapter, table_title or heading,
                          [page.page_number])
            sec.text = table_row_text(hdr, cells)
            sections.append(sec)
            last_row = {"sec": sec, "cells": cells, "hdr": hdr}

    for page, lines in read_lines(pdf_path):
        if page.page_number in skip_pages:
            continue
        tables = page.find_tables() if table_pages == "all" or page.page_number in table_pages else []
        tables = [t for t in tables                               # a real grid, not a ruled box
                  if len(t.rows) >= 2 and len(t.rows[0].cells) >= 2]
        if tables:
            boxes = [t.bbox for t in tables]
            lines = [l for l in lines if not any(b[1] - 2 <= l.top and l.bottom <= b[3] + 2
                                                 for b in boxes)]
            todo = sorted(tables, key=lambda t: t.bbox[1])
        else:
            todo = []
        sizes = sorted(l.size for l in lines) or [10.0]
        body = sizes[len(sizes) // 2]
        toc_page = sum(bool(FIG_RE.match(l.text) and TOC_PAGENO_RE.search(l.text))
                       for l in lines) >= 3
        skip_line = -1
        for i, ln in enumerate(lines):
            if i == skip_line:
                continue
            t = ln.text.strip()
            while todo and ln.top > todo[0].bbox[3]:             # a table sat above this line
                flush()
                emit_table(page, todo.pop(0))
            if not todo and last_row is not None and t:          # text after the table: it is done
                last_row = None
            keyword_lines += len(NOTICE_WORD_RE.findall(t))
            if notice is not None:
                gap = ln.top - notice["bottom"] if ln.page == notice["page"] else 99
                if (gap > 1.8 * max(ln.bottom - ln.top, 6) or chap_re.match(t)
                        or FIG_RE.match(t) or para_re.match(t) or NOTICE_RE.match(t)):
                    end_notice()
                else:
                    notice["parts"].append(t)
                    notice["bottom"], notice["page"] = ln.bottom, ln.page
                    continue
            nm = NOTICE_RE.match(t)
            if nm:
                notice = {"kind": nm["kind"], "parts": [nm["rest"]] if nm["rest"] else [],
                          "bottom": ln.bottom, "page": ln.page}
                continue
            if chap_re.match(t):
                flush()
                chapter, heading = t, ""
                # "Section One:" with its title on the next line in the same type
                # (Aerie): one chapter name, "Section One: Introduction"
                nxt = lines[i + 1] if i + 1 < len(lines) else None
                if t.endswith(":") and nxt and abs(nxt.size - ln.size) < 0.5 and nxt.bold == ln.bold:
                    chapter = f"{t} {nxt.text.strip()}"
                    skip_line = i + 1
                continue
            if FIG_RE.match(t):
                caps = split_captions(t)
                for k, cap in enumerate(caps):
                    if not figures or TOC_LEADER_RE.search(cap["title"]) or (
                            toc_page and TOC_PAGENO_RE.search(cap["title"])):
                        continue
                    w = page.width / len(caps)              # captions side by side share the row
                    xr = (k * w, (k + 1) * w) if len(caps) > 1 else None
                    figs.append(_crop_figure(page, lines, i, manual_id, cap, fig_dir, dpi, xr))
                continue
            if TABLE_RE.match(t):
                table_title = t
                continue
            pm = para_re.match(t) if numbering != "headings" else None
            if pm:
                flush()
                start(Section(f"{manual_id}/{pm['num']}", manual_id, pm["num"], chapter, heading,
                              [ln.page]), pm["rest"])
                continue
            if is_heading(ln, body):
                flush()
                heading = t
                if numbering == "headings":
                    n = per_page[ln.page] = per_page.get(ln.page, 0) + 1
                    num = f"{ln.page}.{n}"
                    start(Section(f"{manual_id}/{num}", manual_id, num, chapter, heading,
                                  [ln.page]), "")
                continue
            if cur is None and numbering != "headings" and heading and not TOC_LEADER_RE.search(t):
                # Text under a heading with no paragraph number (ATP 3-50.21 chapter 2 is
                # all like this). Keep it as a heading-numbered section, "2.h1", "2.h2"...
                # so it can't collide with real paragraph numbers.
                key = chapter_key(chapter)
                n = per_chapter[key] = per_chapter.get(key, 0) + 1
                num = f"{key}.h{n}"
                start(Section(f"{manual_id}/{num}", manual_id, num, chapter, heading, [ln.page]), "")
            if cur is not None:
                if ln.page not in cur.pages:
                    cur.pages.append(ln.page)
                buf.append(t)
        for tbl in todo:                                      # tables below the last line
            flush()
            emit_table(page, tbl)
    flush()
    if pending and sections:
        sections[-1].notices.extend(pending)

    # Keep the first occurrence of a paragraph number (TOC/index false hits come later
    # and are short); warn on duplicates rather than guessing.
    seen: dict[str, Section] = {}
    for s in sections:
        if s.id not in seen or len(s.text) > len(seen[s.id].text):
            seen[s.id] = s
    sections = list(seen.values())

    if numbering == "headings":
        sections = [s for s in sections if s.text or s.notices]
    uniq: dict[str, Figure] = {}
    for f in figs:
        uniq.setdefault(f.id, f)
    figs = list(uniq.values())
    (out_dir / "sections.json").write_text(json.dumps([asdict(s) for s in sections], indent=1))
    # every CAUTION/WARNING/NOTE/DANGER word should be a notice's own keyword or sit inside
    # a notice's text; anything else may be a box the extractor missed
    kept = sum(len(s.notices) for s in sections)
    inside = sum(len(NOTICE_WORD_RE.findall(n["text"])) for s in sections for n in s.notices)
    inline = [f"{s.id}: ...{s.text[max(0, m.start() - 30): m.end() + 40]}..."
              for s in sections for m in INLINE_NOTE_RE.finditer(s.text)]
    other = [f"{s.id} ({f}): {getattr(s, f)[max(0, m.start() - 30): m.end() + 30]}"
             for s in sections for f in ("text", "heading")
             for m in NOTICE_WORD_RE.finditer(getattr(s, f))
             if not (f == "text" and INLINE_NOTE_RE.match(s.text, m.start()))]
    (out_dir / "notices_audit.json").write_text(json.dumps(
        {"keyword_lines": keyword_lines, "notices_kept": kept,
         "missing": max(0, keyword_lines - kept - inside),
         "inline_notes": inline, "keyword_elsewhere": other, "figure_gaps": figure_gaps(figs)}, indent=1))
    (out_dir / "figures.json").write_text(json.dumps([asdict(f) for f in figs], indent=1))
    return sections, figs


def split_captions(t: str) -> list[dict]:
    """"Figure 4-1. Carotid pulse Figure 4-2. Femoral pulse" -> two captions."""
    ms = list(FIG_SPLIT_RE.finditer(t))
    return [{"num": m["num"], "title": t[m.end(): ms[k + 1].start() if k + 1 < len(ms) else None]}
            for k, m in enumerate(ms)]


def figure_gaps(figs: list[Figure]) -> list[str]:
    """Figure numbers missing inside a chapter ("4-1, 4-3" -> "4-2")."""
    by_ch: dict[str, list[int]] = {}
    for f in figs:
        ch, n = f.num.split("-")
        by_ch.setdefault(ch, []).append(int(n))
    return [f"{ch}-{n}" for ch, ns in by_ch.items() for n in range(1, max(ns)) if n not in ns]


def _crop_figure(page, lines: list[Line], idx: int, manual_id: str, m, fig_dir: Path,
                 dpi: int, xr: tuple[float, float] | None = None) -> Figure:
    """Army figures sit above their caption. Take the graphics above the caption, down to
    the last body-text line, and render that box."""
    cap = lines[idx]
    # Graphic objects (raster images + vector strokes) that end above the caption.
    objs = list(page.images) + list(page.rects) + list(page.lines) + list(page.curves)
    above = [o for o in objs if o["bottom"] <= cap.top + 2 and o["top"] >= page.height * 0.05]
    prev_text = max((l.bottom for l in lines[:idx]
                     if l.bottom <= cap.top and len(l.text) > 60), default=page.height * 0.06)
    above = [o for o in above if o["top"] >= prev_text - 2]
    if xr:
        above = [o for o in above if o["x0"] >= xr[0] - 2 and o["x1"] <= xr[1] + 2]
    if above:
        x0 = min(o["x0"] for o in above)
        x1 = max(o["x1"] for o in above)
        top = min(o["top"] for o in above)
    else:
        x0, x1 = xr if xr else (page.width * 0.08, page.width * 0.92)
        top = prev_text
    bbox = [max(0, x0 - 4), max(0, top - 4), min(page.width, x1 + 4), cap.top - 1]
    fig_dir.mkdir(parents=True, exist_ok=True)
    png = fig_dir / f"{manual_id}_fig_{m['num']}.png"
    if bbox[3] - bbox[1] > 10:
        page.crop(bbox).to_image(resolution=dpi).original.convert("L").save(png)
    near = " ".join(l.text for l in lines[max(0, idx - 2): idx + 3])
    return Figure(f"{manual_id}/fig/{m['num']}", manual_id, m["num"], m["title"].strip(),
                  cap.page, bbox, str(png.name), bool(RESTRICTED_RE.search(near)), dpi)


def load_sections(dirs: list[Path]) -> dict[str, dict]:
    out = {}
    for d in dirs:
        p = d / "sections.json"
        if p.exists():
            for s in json.loads(p.read_text()):
                out[s["id"]] = s
    return out


def load_figures(dirs: list[Path]) -> dict[str, dict]:
    out = {}
    for d in dirs:
        p = d / "figures.json"
        if p.exists():
            for f in json.loads(p.read_text()):
                f["path"] = str(d / "figures" / f["png"])
                out[f["id"]] = f
    return out
