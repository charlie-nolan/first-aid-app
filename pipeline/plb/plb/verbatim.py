"""Card text vs the original page: is every on-screen passage word for word what the book prints?

`plb test` already proves each passage is a substring of its *extracted* section. That
only moves the question: did the extractor read the PDF right? (Column merges, a degree
sign read as "o", a dropped line at a page break, two words glued together.) This
re-reads each cited page straight from the PDF with a second, independent text engine
(pdfium, where extraction uses pdfplumber), and compares.

Levels, per passage chunk (a passage is split at its "## WARNING" style notice blocks,
since boxes sit elsewhere on the page):

  exact       same words and punctuation, only whitespace and list markers differ
  layout      same once line-end hyphens, ligatures, curly quotes and dashes are folded
  MISMATCH    words differ: the word-level diff is reported, and (with --judge) an LLM
              is asked only whether the difference is an extraction artifact or changes
              what the card tells someone to do. The judge never clears a card: every
              MISMATCH stays flagged for a person to look at.

Running headers/footers (lines repeated on many pages) are removed before comparing, so
a passage that crosses a page break still matches. Nothing else is skipped: a word the
PDF has and the card lacks is a mismatch, because a dropped "not" would look like that.
"""
from __future__ import annotations

import difflib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# list markers at the start of a line: bullets, "a.", "(1)", "1.", and the paragraph
# number that starts a numbered paragraph ("4-41.", "2-3.", "21.5.8.")
_MARKER = re.compile(r"^\s*(?:[\u2022\u25aa\u25cf\u2013\u2014\-\*o]\s+|\(?[a-z0-9]{1,2}[.)]\s+|"
                     r"\d+(?:-\d+)+\.\s+|\d+(?:\.\d+)+\.?\s+)", re.M)
_FOLD = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-",
         "\u2014": "--", "\u2010": "-", "\u2011": "-", "\u00a0": " ", "\u00ad": "",
         "\u2044": "/", "\ufffe": "-", "\u2002": " ", "\u2003": " ", "\u2009": " ",
         "\u00b0": " degrees ", "\u00ba": " degrees "}   # the extractor spells out degree signs
_NOTICE = re.compile(r"^## [A-Z][A-Z ]+\n", re.M)        # card-side box heading, e.g. "## CAUTION"


def _strip_markers(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _NOTICE.sub("", text)                           # box heading ("CAUTION:" on the page)
    text = re.sub(r"^## ", "", text, flags=re.M)          # card heading marker
    prev = None
    while prev != text:                                     # "- a. " nests
        prev, text = text, _MARKER.sub("", text)
    return text


def exact_form(text: str) -> str:
    t = _strip_markers(text).replace("\ufffe", "-")     # pdfium marks a line-end hyphen
    return " ".join(t.split())


def layout_form(text: str) -> str:
    t = unicodedata.normalize("NFKC", _strip_markers(text))
    t = "".join(_FOLD.get(c, c) for c in t)
    t = re.sub(r"-\s*\n\s*", "", t)                        # line-end hyphen
    return re.sub(r"[\s-]+", "", t)                         # spacing and hyphens are layout


def words(text: str) -> list[str]:
    t = unicodedata.normalize("NFKC", _strip_markers(text))
    t = "".join(_FOLD.get(c, c) for c in t)
    t = re.sub(r"-[ \t]*\n\s*", "-", t)                    # keep a line-end hyphen visible
    return re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?|[^\sA-Za-z0-9]", t)


# ---------------------------------------------------------------- page text

class PdfPages:
    """Page text from pdfium with running headers/footers removed."""

    def __init__(self, pdf: Path):
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(str(pdf))
        raw = []
        for i in range(len(doc)):
            tp = doc[i].get_textpage()
            raw.append(tp.get_text_range().replace("\r\n", "\n").replace("\r", "\n"))
            tp.close()
        doc.close()
        # a running line is one (digits masked) that sits at the top or bottom of many pages
        def key(line):
            return re.sub(r"\d+", "#", line.strip())
        edge = Counter()
        for t in raw:
            lines = [l for l in t.split("\n") if l.strip()]
            for l in set(lines[:3] + lines[-3:]):
                edge[key(l)] += 1
        running = {k for k, n in edge.items() if n >= 3 and len(k) > 2}
        self.pages = []
        for t in raw:
            lines = t.split("\n")
            n = len(lines)
            keep = [l for i, l in enumerate(lines)
                    if not ((i < 4 or i >= n - 4) and (key(l) in running
                                                       or re.fullmatch(r"\s*\d{1,4}\s*", l)))]
            self.pages.append("\n".join(keep))

    def window(self, pages: list[int], pad: int = 1) -> str:
        lo = max(1, min(pages) - pad)
        hi = min(len(self.pages), max(pages) + pad)
        return "\n".join(self.pages[p - 1] for p in range(lo, hi + 1))


class WebPages:
    def __init__(self, root: Path, raw: dict):
        from bs4 import BeautifulSoup
        texts = []
        for pg in raw.get("pages") or [{"file": raw["file"]}]:
            soup = BeautifulSoup((root / pg["file"]).read_text(errors="replace"), "html.parser")
            node = soup.select_one(pg["selector"]) if pg.get("selector") else soup
            texts.append((node or soup).get_text("\n"))
        self.text = "\n".join(texts)

    def window(self, pages, pad=1) -> str:
        return self.text


# ---------------------------------------------------------------- compare

@dataclass
class Finding:
    passage: str
    citation: str
    level: str                      # exact | layout | MISMATCH | NO-SOURCE
    chunk: str = ""
    diff: list[dict] = field(default_factory=list)
    nodes: list[str] = field(default_factory=list)
    judge: dict | None = None


def split_chunks(text: str) -> list[str]:
    """Body text, then each notice block ('## CAUTION\\n...') on its own."""
    parts = re.split(r"\n(?=## [A-Z][A-Z ]+\n)", text)
    return [p for p in parts if p.strip()]


def compare_chunk(chunk: str, window: str) -> tuple[str, list[dict]]:
    if exact_form(chunk) and exact_form(chunk) in exact_form(window):
        return "exact", []
    if layout_form(chunk) and layout_form(chunk) in layout_form(window):
        return "layout", _diff(chunk, window)
    d = _diff(chunk, window)
    return ("MISMATCH", d) if any(not x.get("minor") for x in d) else ("layout", d)


def _diff(chunk: str, window: str) -> list[dict]:
    a, b = words(chunk), words(window)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    m = sm.find_longest_match(0, len(a), 0, len(b))
    if m.size < 3:
        return [{"op": "missing", "card": " ".join(a[:40]),
                 "pdf": "(not found on these pages)", "after": ""}]
    lo = max(0, m.b - m.a - 200)
    hi = min(len(b), m.b + (len(a) - m.a) + 200)
    sm = difflib.SequenceMatcher(None, a, b[lo:hi], autojunk=False)
    ops = sm.get_opcodes()
    # trim the padding we added at either end
    while ops and ops[0][0] == "insert":
        ops = ops[1:]
    while ops and ops[-1][0] == "insert":
        ops = ops[:-1]
    out = []
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            continue
        ctx = " ".join(a[max(0, i1 - 6):i1])
        card, pdf = " ".join(a[i1:i2]), " ".join(b[lo + j1:lo + j2])
        out.append({"op": tag, "card": card, "pdf": pdf, "after": ctx,
                    "minor": layout_form(card) == layout_form(pdf)})
    return out


def check_passages(root: Path, inter: Path, manuals: dict, sections: dict,
                   only_reviewed: bool = True, ids: list[str] | None = None) -> list[Finding]:
    trees = json.loads((inter / "trees.json").read_text())
    passages = {json.loads(l)["id"]: json.loads(l)
                for l in (inter / "passages.jsonl").read_text().splitlines() if l}
    used: dict[str, list[str]] = {}
    for n in trees["nodes"]:
        if only_reviewed and n.get("unreviewed"):
            continue
        for pid in n["passages"]:
            used.setdefault(pid, []).append(n["key"])
    if ids:
        used = {p: k for p, k in used.items() if any(p.startswith(i) for i in ids)}
    cache: dict[str, object] = {}
    out = []
    for pid in sorted(used):
        p = passages.get(pid)
        if p is None:
            continue
        base = pid.split("#")[0].split("~")[0]
        sec = sections.get(base)
        m = manuals.get(p["source"])
        f = Finding(pid, p.get("citation", ""), "NO-SOURCE", nodes=used[pid])
        src = None
        if m is not None and sec is not None:
            if p["source"] not in cache:
                try:
                    if m.get("kind") == "web":
                        cache[p["source"]] = WebPages(root, m)
                    else:
                        pdf = root / m["file"]
                        cache[p["source"]] = PdfPages(pdf) if pdf.exists() else None
                except Exception as e:                       # unreadable PDF: say so
                    cache[p["source"]] = None
                    f.chunk = f"could not read source: {e}"
            src = cache[p["source"]]
        if src is None:
            f.chunk = f.chunk or "source PDF not on this machine"
            out.append(f)
            continue
        window = src.window(sec.get("pages") or [1])
        worst = "exact"
        notes = []
        for ch in split_chunks(p["text"]):
            lvl, diff = compare_chunk(ch, window)
            if lvl == "MISMATCH":
                out.append(Finding(pid, p.get("citation", ""), lvl, ch, diff, used[pid]))
                worst = lvl
            elif lvl == "layout":
                notes += diff
                worst = "layout" if worst == "exact" else worst
        if worst != "MISMATCH":
            out.append(Finding(pid, p.get("citation", ""), worst, diff=notes, nodes=used[pid]))
    return out


# ---------------------------------------------------------------- optional LLM judge

JUDGE_PROMPT = """You check a first-aid device. Its screen may only show text copied word for word
from a printed manual. Below is the CARD text the device will show, and the PAGE text read
from the manual's PDF by a different program. A word-level diff between them is listed.

Decide only this: is every difference an extraction artifact (reading order, a glyph read
wrongly such as a degree sign, words split or joined, a header or figure label caught in
between), or does any difference change, add or remove an instruction, number, warning or
condition that the reader would act on? When unsure, say it changes meaning.

DIFF:
{diff}

CARD:
{card}

PAGE (excerpt):
{page}
"""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["artifact_only", "changes_meaning"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}


def judge(findings: list[Finding], root: Path, inter: Path, manuals: dict, sections: dict,
          model: str = "claude-opus-5-5") -> int:
    """Ask Claude about each MISMATCH. Needs the `anthropic` package and an API key
    (or `ant auth login`). Advisory only: the result is written next to the finding."""
    import anthropic
    client = anthropic.Anthropic()
    n = 0
    for f in findings:
        if f.level != "MISMATCH":
            continue
        diff = "\n".join(f"- card: {d.get('card')!r}  page: {d.get('pdf')!r}" for d in f.diff)
        prompt = JUDGE_PROMPT.format(diff=diff, card=f.chunk, page=_page_excerpt(f, root, manuals,
                                                                                  sections))
        r = client.beta.messages.create(
            model=model, max_tokens=2000,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
            messages=[{"role": "user", "content": prompt}])
        if r.stop_reason == "refusal":
            f.judge = {"verdict": "no_answer", "reason": "model declined"}
        else:
            txt = next((b.text for b in r.content if b.type == "text"), "{}")
            try:
                f.judge = json.loads(txt)
            except json.JSONDecodeError:
                f.judge = {"verdict": "no_answer", "reason": txt[:200]}
        n += 1
    return n


def _page_excerpt(f: Finding, root, manuals, sections, limit: int = 6000) -> str:
    base = f.passage.split("#")[0].split("~")[0]
    sec = sections.get(base) or {}
    m = manuals.get(base.split("/")[0], {})
    src = WebPages(root, m) if m.get("kind") == "web" else PdfPages(root / m["file"])
    w = src.window(sec.get("pages") or [1], pad=0)
    a = words(f.chunk)
    first = " ".join(a[:3])
    i = " ".join(w.split()).find(first) if first else -1
    flat = " ".join(w.split())
    return flat[max(0, i - 500):max(0, i - 500) + limit] if i >= 0 else flat[:limit]


def write_report(findings: list[Finding], path: Path) -> str:
    c = Counter(f.level for f in findings)
    lines = ["# Card text vs source pages", "",
             f"{len({f.passage for f in findings})} passages checked: "
             + ", ".join(f"{c[k]} {k}" for k in ("exact", "layout", "MISMATCH", "NO-SOURCE") if c[k]),
             "", "`exact` = same words and punctuation; `layout` = same once hyphens, "
             "ligatures, quotes and dashes are folded; `MISMATCH` = words differ (listed below).",
             ""]
    for f in findings:
        if f.level in ("MISMATCH", "NO-SOURCE"):
            lines.append(f"## {f.level}: {f.passage} ({f.citation})")
            lines.append(f"Shown on: {', '.join(f.nodes)}")
            if f.judge:
                lines.append(f"Judge (advisory): **{f.judge.get('verdict')}**: {f.judge.get('reason')}")
            for d in f.diff:
                tag = " (spacing/hyphen only)" if d.get("minor") else ""
                lines.append(f"- after \"{d.get('after', '')}\": card `{d['card'] or '(nothing)'}`"
                             f" / page `{d['pdf'] or '(nothing)'}`{tag}")
            if f.level == "NO-SOURCE":
                lines.append(f"- {f.chunk}")
            lines.append("")
    notes = [f for f in findings if f.level == "layout" and f.diff]
    if notes:
        lines += ["## Spacing and hyphen notes", "",
                  "Same words, but the card's spacing or hyphens differ from the print. "
                  "Usually a word broken at a line end; check that the card reads right.", ""]
        for f in notes:
            for d in f.diff:
                lines.append(f"- {f.passage}: card `{d['card'] or '(nothing)'}` / page "
                             f"`{d['pdf'] or '(nothing)'}`")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
    return lines[2]
