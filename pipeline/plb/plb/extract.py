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
FIG_RE = re.compile(r"^Figure\s+(?P<num>(?:[A-Z]|\d{1,2})-\d{1,3})\.\s*(?P<title>.*)$")
TABLE_RE = re.compile(r"^Table\s+(?P<num>(?:[A-Z]|\d{1,2})-\d{1,3})\.\s*(?P<title>.*)$")
CHAPTER_RE = re.compile(r"^(Chapter\s+\d+|Appendix\s+[A-Z])\b", re.I)

# Some DoD PDFs use font subsets whose codes are shifted (e.g. "6HSWHPEHU" = "September").
COMMON = set("""the of and to a in is be or for with it as that by on are from this at can
an not if you your water fire food survival first aid casualty bleeding wound use do
will should may when has have into which all one two any other them than then they""".split())

LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl",
             "‘": "'", "’": "'", "“": '"', "”": '"', "–": "-",
             "—": "--", "•": "*", "©": "(C)", " ": " ", "°": " degrees "}


def normalize(text: str) -> str:
    """Map to the plain ASCII the e-ink font can draw. Anything unmappable becomes '?'."""
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


def read_lines(pdf_path: Path, margin_top: float = 0.07, margin_bottom: float = 0.07):
    """Yield (page_obj, [Line]) skipping running headers/footers."""
    import pdfplumber

    with pdfplumber.open(pdf_path) as doc:
        for pno, page in enumerate(doc.pages, start=1):
            h = page.height
            words = page.extract_words(extra_attrs=["fontname", "size"], keep_blank_chars=False,
                                       use_text_flow=True)
            rows: dict[int, list] = {}
            for w in words:
                if w["top"] < h * margin_top or w["bottom"] > h * (1 - margin_bottom):
                    continue
                rows.setdefault(round(w["top"] / 2), []).append(w)
            lines = []
            for _, ws in sorted(rows.items()):
                ws.sort(key=lambda w: w["x0"])
                text = normalize(" ".join(w["text"] for w in ws))
                # glyphs with no unicode mapping come out as "(cid:N)"; at line start they
                # are almost always bullets, elsewhere they are dropped
                text = re.sub(r"^\(cid:\d+\)\s*", "* ", text)
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


def is_heading(line: Line, body_size: float) -> bool:
    t = line.text.strip()
    if not t or len(t) > 80 or PARA_RE.match(t) or FIG_RE.match(t) or t.endswith("."):
        return False
    return line.bold or line.size > body_size + 0.5 or (t.isupper() and len(t) > 3)


def extract(pdf_path: Path, manual_id: str, out_dir: Path, figures: bool = True,
            dpi: int = 200) -> tuple[list[Section], list[Figure]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    fig_dir = out_dir / "figures"
    sections: list[Section] = []
    figs: list[Figure] = []
    cur: Section | None = None
    buf: list[str] = []
    chapter, heading = "", ""

    def flush():
        nonlocal cur, buf
        if cur is not None:
            cur.text = join_text(buf)
            sections.append(cur)
        cur, buf = None, []

    for page, lines in read_lines(pdf_path):
        sizes = sorted(l.size for l in lines) or [10.0]
        body = sizes[len(sizes) // 2]
        for i, ln in enumerate(lines):
            t = ln.text.strip()
            if CHAPTER_RE.match(t):
                flush()
                chapter, heading = t, ""
                continue
            fm = FIG_RE.match(t)
            if fm:
                if figures:
                    figs.append(_crop_figure(page, lines, i, manual_id, fm, fig_dir, dpi))
                continue
            if TABLE_RE.match(t):
                continue
            pm = PARA_RE.match(t)
            if pm:
                flush()
                cur = Section(f"{manual_id}/{pm['num']}", manual_id, pm["num"], chapter, heading,
                              [ln.page])
                buf = [pm["rest"]]
                continue
            if is_heading(ln, body):
                flush()
                heading = t
                continue
            if cur is not None:
                if ln.page not in cur.pages:
                    cur.pages.append(ln.page)
                buf.append(t)
    flush()

    # Keep the first occurrence of a paragraph number (TOC/index false hits come later
    # and are short); warn on duplicates rather than guessing.
    seen: dict[str, Section] = {}
    for s in sections:
        if s.id not in seen or len(s.text) > len(seen[s.id].text):
            seen[s.id] = s
    sections = list(seen.values())

    (out_dir / "sections.json").write_text(json.dumps([asdict(s) for s in sections], indent=1))
    (out_dir / "figures.json").write_text(json.dumps([asdict(f) for f in figs], indent=1))
    return sections, figs


def _crop_figure(page, lines: list[Line], idx: int, manual_id: str, m, fig_dir: Path,
                 dpi: int) -> Figure:
    """Army figures sit above their caption. Take the graphics above the caption, down to
    the last body-text line, and render that box."""
    cap = lines[idx]
    # Graphic objects (raster images + vector strokes) that end above the caption.
    objs = list(page.images) + list(page.rects) + list(page.lines) + list(page.curves)
    above = [o for o in objs if o["bottom"] <= cap.top + 2 and o["top"] >= page.height * 0.05]
    prev_text = max((l.bottom for l in lines[:idx]
                     if l.bottom <= cap.top and len(l.text) > 60), default=page.height * 0.06)
    above = [o for o in above if o["top"] >= prev_text - 2]
    if above:
        x0 = min(o["x0"] for o in above)
        x1 = max(o["x1"] for o in above)
        top = min(o["top"] for o in above)
    else:
        x0, x1, top = page.width * 0.08, page.width * 0.92, prev_text
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
