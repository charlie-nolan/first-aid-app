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
TITLE_MAX = 28         # Library row fits ~28 chars after the "Unreviewed: " prefix


def _label(s: str, limit: int = LABEL_MAX) -> str:
    s = " ".join(str(s).split()) or "(untitled)"
    return s if len(s) <= limit else s[:limit - 3].rstrip() + "..."


def _chap_key(chap: str | None) -> str:
    """'CHAPTER 2' and 'CHAPTER 2. PERSONAL CLOTHING' are the same chapter."""
    m = re.match(r"^(chapter|appendix)\s+(\S+?)[.:]?(\s|$)", chap or "", flags=re.I)
    return f"{m.group(1).lower()} {m.group(2).upper()}" if m else (chap or "")


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
            blocks.append(chunk(tree, f"{key}.{i // list_max + 1}", f"{first} to {last}", part))
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
        for ci, (_, group) in enumerate(groupby(secs, key=lambda s: _chap_key(s.get("chapter")))):
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
            m_num = re.match(r"^(chapter|appendix)\s+(\S+?)[.:]?(\s|$)", chap, flags=re.I)
            chap_short = (("Ch " if m_num.group(1).lower() == "chapter" else "App ") + m_num.group(2)
                          if m_num else chap)
            chap_label = (f"{chap_short} {first.title() if first.isupper() else first}" if chap else
                          ("Sections" if ci == 0 else f"Sections ({ci + 1})"))
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
