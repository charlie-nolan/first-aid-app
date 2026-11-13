"""Reference shelf: every extracted manual as browsable, UNREVIEWED source text.

Charlie (2026-11-07): ship all converted manuals as a text-only reference shelf, kept
apart from the signed-off topics. Shape:

    manual (tree, shelf "reference", unreviewed)
      -> chapter            (index node: choices only)
         -> heading group   (index node, only when a chapter is long and has headings)
            -> block of N   (index node, only when a list is still too long)
               -> section   (one verbatim passage + its notices, citation on the page)

Rules: no pictures, no voice phrases, never emergency, never linked from a reviewed
tree. Every node and tree carries `unreviewed: true` so firmware can mark the page.
"""
from __future__ import annotations

import re
from itertools import groupby

from .trees import LABEL_MAX, Built, _resolve_cite, pretty_manual

LIST_MAX = 40          # longest choice list we emit; longer lists get another level
COVER_MIN = 400        # front-matter heading sections shorter than this are cover boilerplate
TITLE_MAX = 28         # Library row fits ~28 chars after the "Unreviewed: " prefix


def _label(s: str, limit: int = LABEL_MAX) -> str:
    s = " ".join(str(s).split()) or "(untitled)"
    return s if len(s) <= limit else s[:limit - 3].rstrip() + "..."


WORD_NUMS = ("one two three four five six seven eight nine ten eleven twelve thirteen "
             "fourteen fifteen sixteen seventeen eighteen nineteen twenty").split()


def _chap_key(chap: str | None) -> str:
    """'CHAPTER 2' and 'CHAPTER 2. PERSONAL CLOTHING' are the same chapter; so are
    'Section Four: Traumatic Emergencies' lines (Aerie), keyed as chapter 4."""
    m = re.match(r"^(chapter|appendix)\s+(\S+?)[.:]?(\s|$)", chap or "", flags=re.I)
    if m:
        return f"{m.group(1).lower()} {m.group(2).upper()}"
    w = re.match(r"^section\s+([a-z]+)\b", chap or "", flags=re.I)
    if w and w.group(1).lower() in WORD_NUMS:
        return f"chapter {WORD_NUMS.index(w.group(1).lower()) + 1}"
    return chap or ""


def _chap_sort(key: str):
    """Front matter, then Chapter 1..n, then Appendix A..Z, then anything else."""
    m = re.match(r"^(chapter|appendix) (\S+)$", key)
    if not key:
        return (0, 0, "")
    if m and m.group(1) == "chapter" and m.group(2).isdigit():
        return (1, int(m.group(2)), "")
    if m and m.group(1) == "appendix":
        return (2, 0, m.group(2))
    return (3, 0, key)


def _first_words(text: str) -> str:
    return " ".join(text.replace("\n", " ").split()[:8])


def add_reference_shelf(b: Built, sections_by_manual: dict[str, list[dict]],
                        manuals: dict[str, dict], list_max: int = LIST_MAX) -> int:
    """Append one reference tree per manual to `b`. Returns the number of nodes added."""
    next_id = max((n["id"] for n in b.nodes), default=-1) + 1
    start = next_id
    reviewed_trees = {t["id"] for t in b.trees}

    def node(tree, key, label, passages=(), choices=()):
        nonlocal next_id
        n = {"id": next_id, "key": f"{tree}/{key}", "tree": tree, "label": _label(label),
             "passages": list(passages), "lookalikes": [], "figure": None,
             "choices": list(choices), "species": False, "no_match": False,
             "emergency": False, "tags": {}, "unreviewed": True}
        next_id += 1
        b.nodes.append(n)
        return n

    def link(n):
        return {"label": n["label"], "to": n["id"], "kind": "next"}

    def chunk(tree, key, label, kids):
        """An index node over `kids`, splitting into numbered blocks if too long."""
        if len(kids) <= list_max:
            return node(tree, key, label, choices=[link(k) for k in kids])
        blocks = []
        for i in range(0, len(kids), list_max):
            part = kids[i:i + list_max]
            first, last = part[0]["label"].split(" ")[0], part[-1]["label"].split(" ")[0]
            if re.match(r"^[A-Z0-9]{1,3}[-.]\d", first) and re.match(r"^[A-Z0-9]{1,3}[-.]\d", last):
                blabel = f"{first} to {last}"            # numbered paragraphs: "2-1 to 2-40"
            else:                                         # headings: position + first title
                blabel = f"{i + 1}-{i + len(part)}: {part[0]['label']}"
            blocks.append(chunk(tree, f"{key}.{i // list_max + 1}", blabel, part))
        return chunk(tree, key, label, blocks) if len(blocks) > list_max else \
            node(tree, key, label, choices=[link(k) for k in blocks])

    for mid, secs in sections_by_manual.items():
        if not secs:
            continue
        tid = f"ref-{mid}"
        if tid in reviewed_trees:
            continue
        title = pretty_manual(mid, manuals)
        m = manuals.get(mid, {})
        short = m.get("topic") or m.get("short") or title
        about = " ".join(x for x in [m.get("about") or "",
                                     f"{title}, {str(m.get('edition')).split(' (')[0]}." if str(m.get("edition") or "TBD") != "TBD" else f"{title}."] if x)
        chapters = []
        # Group by chapter over the whole manual (heading-only ".h" sections are emitted
        # after the numbered ones, and TOC lines repeat chapter names), then put chapters
        # in book order and sections in page order.
        buckets: dict[str, list] = {}
        for i, s in enumerate(secs):
            buckets.setdefault(_chap_key(s.get("chapter")), []).append((i, s))
        ordered = []
        first_page = {k: min((s.get("pages") or [0])[0] for _, s in v) for k, v in buckets.items()}
        for k in sorted(buckets, key=lambda k: (_chap_sort(k)[:2], first_page[k], k)):
            ordered += [s for _, s in sorted(buckets[k], key=lambda p: ((p[1].get("pages") or [0])[0], p[0]))]
        n_chaps = len(buckets)
        if n_chaps > 1 and "" in buckets:
            # cover-page fragments (date, "Headquarters...", distribution line, signature
            # block) are heading-only sections in the front matter with almost no text
            ordered = [s for s in ordered if _chap_key(s.get("chapter")) or ".h" not in s["para"]
                       or len(s["text"]) + sum(len(n.get("text", "")) for n in s.get("notices") or [])
                       >= COVER_MIN]
        secs_eff = ordered
        for ci, (_, group) in enumerate(groupby(secs_eff, key=lambda s: _chap_key(s.get("chapter")))):
            group = list(group)
            chap_lines = list(dict.fromkeys(s.get("chapter") or "" for s in group))
            chap = chap_lines[0]
            leaves = []
            # a section's label is its heading, unless neighbours share that heading:
            # then para + first words, so a list never repeats one title
            runs = [list(g) for _, g in groupby(group, key=lambda s: s.get("heading") or "")]
            for run in runs:
                for s in run:
                    pids = _resolve_cite(s["id"], {s["id"]: s}, manuals, tid, b)
                    head = s.get("heading") or ""
                    if ".h" in s["para"]:         # heading-only section: no real para number
                        h = re.sub(r"^[\s*#\-]+", "", head).strip()
                        label = (h.title() if h.isupper() else h) if h and len(run) == 1 \
                            else _first_words(s["text"])
                    else:
                        label = f"{s['para']} {head if len(run) == 1 and head else _first_words(s['text'])}"
                    leaves.append(node(tid, f"s{s['para']}", label, passages=pids))
            heads = [list(g) for _, g in groupby(zip(group, leaves),
                                                  key=lambda p: p[0].get("heading") or "")]
            # chapter title: from a "CHAPTER 2. PERSONAL CLOTHING" line if there is one,
            # else the first ALL-CAPS heading (sidebar vignettes like "Russia, 1942" are not)
            titled = [re.sub(r"^\S+\s+\S+?[.:]?\s+", "", c, count=1) for c in chap_lines
                      if re.match(r"^\S+\s+\S+?[.:]?\s+\S", c)]
            def plain(h):
                return re.sub(r"[^a-z0-9]", "", h.lower())
            heads_ok = [s.get("heading") for s in group if s.get("heading")
                        and "contents" not in s.get("heading", "").lower()
                        and plain(s["heading"]) != plain(title)]       # running headers
            first = titled[0] if titled else next((h for h in heads_ok if h.isupper()), "")
            first = re.sub(r"\s*\.{3,}.*$", "", first).strip()          # TOC dot leaders
            m_num = re.match(r"^(chapter|appendix)\s+(\S+?)[.:]?(\s|$)", chap, flags=re.I)
            chap_short = (("Ch " if m_num.group(1).lower() == "chapter" else "App ") + m_num.group(2)
                          if m_num else chap)
            chap_label = (f"{chap_short} {first.title() if first.isupper() else first}" if m_num
                          else chap if chap else
                          ("Front matter" if n_chaps > 1 else "Sections") if ci == 0
                          else f"Sections ({ci + 1})")
            if len(leaves) > list_max and 1 < len(heads) <= list_max * list_max:
                kids = [chunk(tid, f"c{ci}h{hi}", f"{h[0][1]['label'].split(' ')[0]} "
                              f"{h[0][0].get('heading') or ''}", [l for _, l in h])
                        if len(h) > 1 else h[0][1] for hi, h in enumerate(heads)]
                chapters.append(chunk(tid, f"c{ci}", chap_label, kids))
            else:
                chapters.append(chunk(tid, f"c{ci}", chap_label, leaves))
        root = chapters[0] if len(chapters) == 1 else chunk(tid, "root", title, chapters)
        # the root sits first in the tree's own node order for readers that assume it
        b.trees.append({"id": tid, "title": _label(short, TITLE_MAX),
                        "full_title": m.get("title") and f"{title}: {m['title']}" or title,
                        "about": about, "shelf": "reference",
                        "root": root["id"], "emergency": False, "tags": {},
                        "unreviewed": True})
    for n in b.nodes:
        n.setdefault("unreviewed", False)
    for t in b.trees:
        t.setdefault("unreviewed", False)
    return next_id - start
