"""Saved US Government web pages (NWS, Ready.gov, ...) -> sections, like extract.py does for PDFs.

A web "manual" in sources.yaml has `kind: web` and a list of `pages`, each with its own
file, url, sha256 and optional CSS `selector` for the article body. Every page becomes a
chapter (its <title>), and every heading on it starts a section. Section paras are
"<page>.h<n>": they have no printed paragraph number, so they cite as
"NWS Lightning Safety, <page title>, <heading>" (see trees.citation_for).

Text is taken verbatim from the HTML (whitespace collapsed, mapped to ASCII by the same
normalize() as the PDFs). Pictures, navigation, scripts and "Related content" blocks are
dropped. Nothing is reworded.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

from .extract import normalize

DEFAULT_SELECTORS = ("article", "main", ".cms-content", "#content")
BLOCK = {"p", "div", "li", "ul", "ol", "section", "article", "table", "tr", "td", "th", "tbody",
         "thead", "blockquote", "dl", "dt", "dd", "header", "footer", "figure", "main", "aside"}
HEAD = {"h1", "h2", "h3", "h4", "h5", "h6"}
SKIP = {"script", "style", "noscript", "nav", "img", "svg", "form", "button", "iframe", "figure",
        "figcaption", "select", "input", "video", "audio", "picture"}
STOP_HEADINGS = re.compile(r"^(related content|related resources|share this page|"
                           r"associated content|additional resources|last updated)", re.I)


BR = "\u2028"                 # <br> survives whitespace collapsing as a line break
BOILERPLATE = re.compile(r"^If you are a disaster survivor, please visit FEMA\.gov")


def _clean(s: str) -> str:
    lines = (" ".join(normalize(part).split()) for part in s.split(BR))
    return "\n".join(l for l in lines if l)


def _text(el) -> str:
    return str(el) if isinstance(el, NavigableString) else el.get_text()


def _strip(root) -> None:
    """Remove what a reader never sees as article text."""
    for junk in root.find_all(SKIP):
        junk.decompose()
    for sel in ("span.control", ".visually-hidden", ".sr-only", ".usa-sr-only",
                ".breadcrumb", ".one-third-last", "a.fema-link"):
        # NWS "+" toggles, screen-reader labels, NWS breadcrumb and sidebar, Ready.gov jump menu
        for junk in root.select(sel):
            junk.decompose()
    for br in root.find_all("br"):
        br.replace_with(NavigableString(BR))


def page_title(soup: BeautifulSoup) -> str:
    t = soup.head.find("title", recursive=False) if soup.head else None
    t = _clean(t.get_text()) if t else ""
    return re.sub(r"\s*\|\s*Ready\.gov$", "", t).strip()


def content_root(soup: BeautifulSoup, selector: str | None):
    for sel in ([selector] if selector else []) + list(DEFAULT_SELECTORS):
        found = soup.select(sel)
        if found:
            return max(found, key=lambda e: len(e.get_text()))
    return soup.body or soup


def blocks(root) -> list[tuple[str, str]]:
    """[(kind, text)] in reading order; kind is 'h' (heading), 'p' or 'li'."""
    out: list[tuple[str, str]] = []
    buf: list[str] = []

    def flush():
        t = _clean("".join(buf))
        buf.clear()
        if t and t != "+" and not BOILERPLATE.match(t):
            out.append(("p", t))

    def walk(el):
        for ch in list(el.children):
            if isinstance(ch, NavigableString):
                if type(ch).__name__ in ("Comment", "Doctype", "ProcessingInstruction"):
                    continue
                buf.append(str(ch))
                continue
            if not isinstance(ch, Tag) or ch.name in SKIP:
                continue
            if ch.name in HEAD:
                flush()
                t = _clean(_text(ch))
                if t:
                    out.append(("h", t))
            elif ch.name == "li":
                flush()
                if ch.find(["ul", "ol", "p", "div"]):
                    # nested list: the item's own words, then its children
                    own = _clean("".join(_text(c) for c in ch.children
                                         if isinstance(c, NavigableString) or c.name not in BLOCK))
                    if own:
                        out.append(("li", own))
                    walk_children_blocks(ch)
                else:
                    t = _clean(_text(ch))
                    if t:
                        out.append(("li", t))
            elif ch.name in BLOCK:
                flush()
                walk(ch)
                flush()
            else:                                   # inline: a, span, strong, em, sup...
                buf.append(_text(ch))
        return out

    def walk_children_blocks(li):
        for c in li.children:
            if isinstance(c, Tag) and c.name in BLOCK:
                walk(c)
                flush()

    walk(root)
    flush()
    return out


def page_sections(html: str, manual_id: str, page_no: int, selector: str | None = None,
                  title: str | None = None) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    title = title or page_title(soup)
    secs: list[dict] = []
    head, lines = "", []

    def emit():
        if lines:
            n = len(secs) + 1
            secs.append({"id": f"{manual_id}/{page_no}.h{n}", "manual": manual_id,
                         "para": f"{page_no}.h{n}", "chapter": title, "heading": head or title,
                         "pages": [page_no], "text": "\n".join(lines), "notices": []})

    body = content_root(soup, selector)
    _strip(body)
    for kind, t in blocks(body):
        if kind == "h":
            t = t.replace("\n", " ")
            if STOP_HEADINGS.match(t):
                break
            if t == title and not secs and not lines:
                continue                            # the page's own <h1>
            emit()
            head, lines = t, []
        else:
            lines.append(f"- {t}" if kind == "li" else t)
    emit()
    return secs


def extract_web(root: Path, m: dict, out: Path) -> list[dict]:
    secs = []
    for i, pg in enumerate(m["pages"], 1):
        html = (root / pg["file"]).read_text(encoding="utf-8", errors="replace")
        secs += page_sections(html, m["id"], i, pg.get("selector"), pg.get("title"))
    out.mkdir(parents=True, exist_ok=True)
    (out / "sections.json").write_text(json.dumps(secs, indent=1))
    (out / "figures.json").write_text("[]")
    return secs
