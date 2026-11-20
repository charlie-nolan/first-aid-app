"""Keyword search mode (Charlie 11-20): say ONE word, browse every related card in a 4x2 grid.

No context analysis, no slot filling, no life-rule jumps: a word (or a short phrase) goes in, a
ranked list of nodes comes out, and the person picks. The device pages it 8 tiles at a time.

Where a node's words come from (a word's weight on a node = the SUM of the best weight from each
distinct source, so "tourniquet" in a card's label AND its phrases beats a question that only
mentions it in its label) (keyed with vr.form._key, so choke/choking/choked all match):

    weight 6  tree title, tree condition names (vr.form.topic_phrases) -> the tree's root tile
    weight 3  the node's own label
    weight 2  the node's own phrases; slot phrases whose answer leads to the node (questions
              choices) or whose life rule goes to it (life_threat)
    weight 1  the tree's phrases (root), choice labels pointing at the node
    weight 1  every node of a tree whose title/condition name matched (browse the whole topic)

Ranking (a C port must reproduce it exactly):
    1. life cards first: any node some tree's life_threat rule goes to ("tree/card" keys resolved
       to their own tree), but only when its score is at least 2 and at least a third of the best score
       for the query (score >= 2 and 3 * score >= top), so a weak side match never buries the topic (PC check de06aaa: "eye")
    2. score: sum over query words of that word's weight on the node; a node must
       match EVERY query word (one-word queries: that word)
    3. root tiles before inner nodes, then depth from the root (BFS), then node id
Not indexed: phrases of shared blanks (vr.live._shared_slots: CPR's own blanks and any blank 3+
trees carry, plus UNIVERSAL awake/breathing) except in their home tree, and the UNIVERSAL life
phrases themselves ("won't open her eyes"), which say nothing about a topic.
Duplicates: a node is listed once under its own tree ("wfa-bleeding/severe" never twice).

    python -m vr.search --trees DIR [DIR] --slots SLOTS_DIR WORD [WORD ...]
    python -m vr.search --trees DIR [DIR] --slots SLOTS_DIR --parity OUT.jsonl --words FILE

Parity frames (kind "search"): {"v": 1, "query", "hits": N, "pages": [[{"title", "node",
"life"}, ...8], ...]} with at most MAX_HITS hits. Titles hold tree labels: keep personal outputs local.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .form import _FILLER, _key, node_id, topic_phrases
from .intake import UNIVERSAL, tokenize

PAGE = 8          # 4 x 2 grid
MAX_HITS = 48     # 6 pages; past that the word is too vague to browse
STOP = _FILLER | {"he", "she", "they", "we", "you", "me", "him", "her", "his", "their", "our", "with",
                  "for", "at", "from", "by", "be", "was", "are", "has", "have", "had", "do", "does",
                  "did", "not", "this", "that", "what", "how", "when", "where", "who", "if", "so",
                  "can", "will", "should", "up", "out", "off", "over", "after", "before", "one",
                  "someone", "somebody", "something", "help", "please", "get", "got", "go", "very"}


ALIASES = {"blood": ["bleed"]}   # query side only: "blood" also finds the bleeding cards


def _keys(text) -> set:
    return {_key(w) for w in tokenize(str(text or "")) if w not in STOP}


def _depths(tree: dict) -> dict:
    root, nodes = tree.get("root", "start"), tree.get("nodes") or {}
    d, todo = {root: 0}, [root]
    while todo:
        n = todo.pop(0)
        for c in (nodes.get(n) or {}).get("choices") or []:
            t = c.get("to")
            if t in nodes and t not in d:
                d[t] = d[n] + 1
                todo.append(t)
    return d


def _resolve(name: str, card: str, trees: dict) -> tuple[str, str] | None:
    if name in trees and card in (trees[name][0].get("nodes") or {}):
        return name, card
    if "/" in card:
        t, c = card.split("/", 1)
        if t in trees and c in (trees[t][0].get("nodes") or {}):
            return t, c
    return None


def build_index(trees: dict) -> dict:
    """{(tree, node): {"words": {key: weight}, "topic": {key}, "life": bool, "root": bool,
    "depth": int, "title": str}} for every node of every tree."""
    idx = {}
    def add(at, words, w, src):
        if at is None or at not in idx:
            return
        bag = idx[at]["words"]
        for k in words:
            per = bag.setdefault(k, {})
            per[src] = max(per.get(src, 0), w)

    for name, (tree, smap) in trees.items():
        nodes, root, dep = tree.get("nodes") or {}, tree.get("root", "start"), _depths(tree)
        topic = _keys(tree.get("title", name)) | {k for t in topic_phrases(name, tree, smap or {}) for k in t}
        for nid, n in nodes.items():
            idx[(name, nid)] = {"words": {}, "topic": topic, "life": False, "root": nid == root,
                                "depth": dep.get(nid, 99), "tree": tree.get("title", name),
                                "label": str(n.get("label", nid))}
            add((name, nid), _keys(n.get("label")), 3, "label")
            for ph in n.get("phrases") or []:
                add((name, nid), _keys(ph), 2, "phrase")
            for c in n.get("choices") or []:
                if c.get("to") in nodes:
                    add((name, c["to"]), _keys(c.get("label")), 1, "choice")
        add((name, root), topic, 6, "topic")
        for ph in tree.get("phrases") or []:
            add((name, root), _keys(ph), 1, "tree")

    from .live import GENERIC_HOME, _shared_slots
    shared = _shared_slots(trees) | set(UNIVERSAL)
    life_ph = {tuple(tokenize(p)) for vs in UNIVERSAL.values() for ps in vs.values() for p in ps}
    for name, (tree, smap) in trees.items():
        smap = smap or {}
        own = (smap.get("slots") or {}) if name in GENERIC_HOME or smap.get("generic_home") else \
            {k: v for k, v in (smap.get("slots") or {}).items() if k not in shared}
        slots = {k: {v: [p for p in ps if tuple(tokenize(p)) not in life_ph] for v, ps in vs.items()}
                 for k, vs in own.items()}
        for node, q in (smap.get("questions") or {}).items():
            for value, to in (q.get("choices") or {}).items():
                at = _resolve(name, to, trees)
                for ph in (slots.get(q.get("slot")) or {}).get(value) or []:
                    add(at, _keys(ph), 2, "slot")
        lt = smap.get("life_threat") or []
        rules = [{"if": {s: v}, "go": g} for s, m in lt.items() for v, g in m.items()] if isinstance(lt, dict) else lt
        for r in rules:
            at = _resolve(name, r["go"], trees)
            for s, v in (r.get("if") or {}).items():
                for ph in (slots.get(s) or {}).get(v) or []:
                    add(at, _keys(ph), 2, "slot")
        for r in rules:
            at = _resolve(name, r["go"], trees)
            if at:
                idx[at]["life"] = True
    return idx


def search(query: str, trees: dict, index: dict | None = None, limit: int = MAX_HITS) -> list[dict]:
    """Ranked hits for a spoken word: [{"tree", "card", "node", "title", "life", "score"}]."""
    idx = index if index is not None else build_index(trees)
    q = sorted(_keys(query))
    if not q:
        return []
    found = []
    for (name, nid), e in idx.items():
        score = 0
        for k in q:
            w = max(sum((e["words"].get(a) or {}).values()) for a in [k] + ALIASES.get(k, []))
            if not w and k in e["topic"]:
                w = 1                      # the word names the topic: browse every card in it
            if not w:
                break
            score += w
        else:
            found.append((score, name, nid, e))
    top = max((f[0] for f in found), default=0)
    hits = sorted((not (e["life"] and sc >= 2 and 3 * sc >= top), -sc, not e["root"], e["depth"], n, c, e)
                  for sc, n, c, e in found)
    return [{"tree": n, "card": c, "node": node_id(n, c, trees[n][0]), "title": f"{e['tree']} > {e['label']}",
             "life": e["life"], "score": -s} for _, s, _, _, n, c, e in hits[:limit]]


def pages(hits: list[dict], size: int = PAGE) -> list[list[dict]]:
    return [hits[i:i + size] for i in range(0, len(hits), size)]


def frame(query: str, trees: dict, index: dict | None = None) -> dict:
    hits = search(query, trees, index)
    return {"v": 1, "kind": "search", "query": query[:100], "hits": len(hits),
            "pages": [[{"title": h["title"][:78], "node": h["node"][:78], "life": h["life"]} for h in p]
                      for p in pages(hits)]}


def main(argv=None):
    from .live import load_all
    ap = argparse.ArgumentParser(prog="vr.search")
    ap.add_argument("--trees", type=Path, nargs="+", required=True)
    ap.add_argument("--slots", type=Path, required=True)
    ap.add_argument("--parity", type=Path, help="write one search frame per word to this jsonl")
    ap.add_argument("--words", type=Path, help="file of query words/phrases, one per line")
    ap.add_argument("query", nargs="*")
    a = ap.parse_args(argv)
    trees = load_all(a.trees, a.slots)
    idx = build_index(trees)
    qs = list(a.query)
    if a.words:
        qs += [l.strip() for l in a.words.read_text().splitlines() if l.strip() and not l.startswith("#")]
    if a.parity:
        with open(a.parity, "w") as out:
            for q in qs:
                out.write(json.dumps(frame(q, trees, idx)) + "\n")
        print(f"{len(qs)} queries -> {a.parity}")
        return
    for q in qs:
        hits = search(q, trees, idx)
        print(f"== {q}: {len(hits)} hits")
        for i, p in enumerate(pages(hits)):
            print(f"  page {i + 1}")
            for h in p:
                print(f"    {'!' if h['life'] else ' '} {h['score']:>2}  {h['node']:<34} {h['title']}")


if __name__ == "__main__":
    main()
