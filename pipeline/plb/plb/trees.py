"""Fallback trees: load YAML, enforce the content rules, resolve citations to verbatim text.

The builder never writes advice. A node's on-screen body is ONLY text copied out of a
manual section (whole paragraph, or a verbatim from..to excerpt of one). The only text a
person types into a tree is a short navigation label ("Have a tourniquet?").

Tree YAML (content/trees/*.yaml):

    id: bleeding
    title: Bleeding
    shelf: first-aid            # first-aid | survival | bushcraft | plants | fire | traps | weather
    emergency: true             # usable from the low-battery reserve; never filtered
    tags: {region: [all, -fl, -hi], season: [winter]}   # optional context filter
    root: start
    phrases: [bleeding, "bleeding won't stop"]    # open the root node
    nodes:
      start:
        label: Bleeding hard?
        cite:
          - ATP4-02.11/3-12                          # whole paragraph
          - {ref: ATP4-02.11/3-13, from: "Apply", to: "wound."}   # verbatim excerpt
        figure: ATP4-02.11/fig/3-4
        phrases: [severe bleeding, {say: "help bleeding", always: true}]
        tags: {activity: [water]}   # node tags override the tree's, per dimension
        emergency: true             # this one node is never filtered
        choices:
          - {label: Have a tourniquet, to: tq, kind: kit}
          - {label: Can improvise one, to: tq_improv, kind: improvised}
          - {label: Nothing, to: pressure, kind: nothing}

The shape of this file (keys, types, key spelling) is checked first by the Pydantic
schema in plb/schema.py; `plb validate` runs that check alone, without the manuals.

Tags are checked against content/contexts.yaml (default: 50 states + DC by postal
code, groups like pnw, seasons, activities). Untagged = global.

Plant-shelf leaves must be either `species: true` with `lookalikes:` citations, or
`no_match: true` (which shows the fixed NO_MATCH string from content/strings.yaml).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

KINDS = {"kit", "improvised", "nothing", "next"}
SHELVES = {"first-aid", "survival", "bushcraft", "plants", "fire", "traps", "weather"}
# "reference" is reserved for the generated, unreviewed manual shelf (plb/shelf.py)
LABEL_MAX = 40
BANNED_PLANT = re.compile(r"\b(mushroom|fung(us|i)|toadstool|morel|chanterelle|puffball)", re.I)


STATES = ("ak al ar az ca co ct dc de fl ga hi ia id il in ks ky la ma md me mi mn mo ms mt nc "
          "nd ne nh nj nm nv ny oh ok or pa ri sc sd tn tx ut va vt wa wi wv wy").split()
DEFAULT_CONTEXTS = {"season": ["spring", "summer", "fall", "winter"],
                    "activity": ["hiking", "water", "vehicle", "camp"],
                    "groups": {"pnw": ["wa", "or", "id"]}}


TAG_BITS = {"region": 64, "season": 4, "activity": 8}   # romkit FORMAT.md NTAG layout


def vocab(contexts: dict) -> dict[str, set[str]]:
    def ids(xs):
        return {x["id"] if isinstance(x, dict) else x for x in xs}
    v = {"region": ids(contexts.get("region") or STATES),
         "season": ids(contexts.get("season") or DEFAULT_CONTEXTS["season"]),
         "activity": ids(contexts.get("activity") or DEFAULT_CONTEXTS["activity"])}
    v["region"] |= set((contexts.get("groups") or {}).keys())
    return v


@dataclass
class Issue:
    level: str   # "error" | "warning"
    where: str
    msg: str

    def __str__(self):
        return f"{self.level.upper():7} {self.where}: {self.msg}"


@dataclass
class Passage:
    id: str
    source: str
    para: str
    heading: str
    citation: str
    text: str


@dataclass
class Built:
    trees: list[dict] = field(default_factory=list)
    nodes: list[dict] = field(default_factory=list)
    passages: dict[str, Passage] = field(default_factory=dict)
    figures_used: set[str] = field(default_factory=set)
    # (phrase, node, where, always, tree_id, is_entry)
    node_phrases: list[tuple[str, int, str, bool, str, bool]] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self):
        return [i for i in self.issues if i.level == "error"]


def pretty_manual(manual_id: str, manuals: dict[str, dict]) -> str:
    m = manuals.get(manual_id, {})
    return m.get("cite_as") or re.sub(r"^([A-Z]+)(\d)", r"\1 \2", manual_id)


def citation_for(manual_id: str, para: str, manuals: dict[str, dict],
                 sec: dict | None = None) -> str:
    """'ATP 4-02.11, para 4-37'. Heading-only sections ("2.h1") have no real paragraph
    number, so they cite their place instead: 'front matter, Preface' or 'Ch 2, <heading>'."""
    book = pretty_manual(manual_id, manuals)
    if manuals.get(manual_id, {}).get("cite_style") == "page" and sec:
        # 'Aerie WM 15e, p42, Splinting': books without paragraph numbers cite the page
        head = " ".join(re.sub(r"^[\s*#\-]+", "", sec.get("heading") or "").split())
        head = head.title() if head.isupper() else head
        head = head if len(head) <= 40 else head[:37].rstrip() + "..."
        page = (sec.get("pages") or [None])[0]
        page = page - int(manuals[manual_id].get("page_offset") or 0) if page else page
        return ", ".join(x for x in [book, f"p{page}" if page else "", head] if x)
    if ".h" not in para:
        return f"{book}, para {para}"
    sec = sec or {}
    head = " ".join(re.sub(r"^[\s*#\-]+", "", sec.get("heading") or "").split())
    head = head.title() if head.isupper() else head
    head = head if len(head) <= 40 else head[:37].rstrip() + "..."
    m = re.match(r"^(chapter|appendix)\s+(\S+?)[.:]?(\s|$)", sec.get("chapter") or "", flags=re.I)
    where = ((("Ch " if m.group(1).lower() == "chapter" else "App ") + m.group(2)) if m
             else (sec.get("chapter") or "front matter"))
    return f"{book}, {where}, {head}" if head and head != where else f"{book}, {where}"


def load_trees(tree_dir) -> list[tuple[Path, dict]]:
    """tree_dir: one folder, or a list (content/trees, then content/trees_personal)."""
    out = []
    dirs = tree_dir if isinstance(tree_dir, (list, tuple)) else [tree_dir]
    for p in [p for d in dirs for p in sorted(Path(d).glob("*.yaml"))]:
        data = yaml.safe_load(p.read_text())
        if data:
            out.append((p, data))
    return out


def _resolve_cite(c, sections, manuals, where, b: Built) -> list[str]:
    """Passage ids for one citation: the cited text, then every CAUTION/WARNING/NOTE box
    attached to that paragraph (section["notices"]), so a step never ships without its
    warning."""
    if isinstance(c, str):
        c = {"ref": c}
    ref = c.get("ref", "")
    sec = sections.get(ref)
    if sec is None:
        b.issues.append(Issue("error", where, f"cites {ref!r}, which is not an extracted section"))
        return []
    text = sec["text"]
    pid = ref
    if "from" in c or "to" in c:
        a, z = c.get("from"), c.get("to")
        i = text.find(a) if a else 0
        if i < 0:
            b.issues.append(Issue("error", where, f"excerpt start {a!r} not found verbatim in {ref}"))
            return []
        j = text.find(z, i) if z else len(text)
        if j < 0:
            b.issues.append(Issue("error", where, f"excerpt end {z!r} not found after start in {ref}"))
            return []
        text = text[i:j + (len(z) if z else 0)]
        pid = f"{ref}#{i}-{i + len(text)}"
    if pid not in b.passages:
        b.passages[pid] = Passage(pid, sec["manual"], sec["para"], sec.get("heading", ""),
                                  citation_for(sec["manual"], sec["para"], manuals, sec), text)
    out = [pid]
    for i, nt in enumerate(sec.get("notices") or [], 1):
        kind = str(nt.get("kind", "NOTE")).upper()
        nid = f"{ref}~{kind.lower()}{i}"
        if nid not in b.passages:
            b.passages[nid] = Passage(nid, sec["manual"], sec["para"], kind,
                                      citation_for(sec["manual"], sec["para"], manuals, sec),
                                      f"## {kind}\n{nt['text']}")
        out.append(nid)
    return out


def _check_tags(tags, where: str, voc: dict[str, set[str]], b) -> dict:
    if tags is None:
        return {}
    if not isinstance(tags, dict):
        b.issues.append(Issue("error", where, "tags must be a map like {region: [wa, or]}"))
        return {}
    out = {}
    for dim, vals in tags.items():
        if dim not in voc:
            b.issues.append(Issue("error", where, f"unknown tag dimension {dim!r}"))
            continue
        vals = [vals] if isinstance(vals, str) else list(vals or [])
        for v in vals:
            base = str(v).lower().lstrip("-")
            if base != "all" and base not in voc[dim]:
                b.issues.append(Issue("error", where, f"unknown {dim} tag {v!r}"))
        if vals:
            out[dim] = [str(v).lower() for v in vals]
    return out


def build_trees(tree_dir: Path, sections: dict[str, dict], figures: dict[str, dict],
                manuals: dict[str, dict], contexts: dict | None = None) -> Built:
    b = Built()
    voc = vocab(contexts or DEFAULT_CONTEXTS)
    for dim, n in TAG_BITS.items():
        groups = set((contexts or {}).get("groups") or {}) if dim == "region" else set()
        if len(voc[dim] - groups) > n:
            b.issues.append(Issue("error", "contexts.yaml", f"{len(voc[dim] - groups)} {dim} values, "
                                                            f"the ROM has {n} bits"))
    from .schema import validate_tree
    loaded = []
    for path, t in load_trees(tree_dir):
        bad = validate_tree(t, path.name)
        b.issues += bad
        if not bad:                # a malformed tree is reported once, by the schema, and skipped
            loaded.append((path, t))
    # pass 1: assign dense node ids
    key_to_id: dict[str, int] = {}
    for _, t in loaded:
        keys = list(t.get("nodes") or {})
        if t.get("root") in keys:              # root first, then file order
            keys.remove(t["root"])
            keys.insert(0, t["root"])
        for k in keys:
            key_to_id[f"{t.get('id')}/{k}"] = len(key_to_id)

    for path, t in loaded:
        tid = t.get("id")
        where = f"{path.name}"
        for req in ("id", "title", "shelf", "root", "nodes"):
            if req not in t:
                b.issues.append(Issue("error", where, f"missing '{req}'"))
        if not tid or "nodes" not in t:
            continue
        if t.get("shelf") not in SHELVES:
            b.issues.append(Issue("error", where, f"unknown shelf {t.get('shelf')!r}"))
        root_key = f"{tid}/{t.get('root')}"
        if root_key not in key_to_id:
            b.issues.append(Issue("error", where, f"root {t.get('root')!r} is not a node"))
            continue
        plants = t.get("shelf") == "plants"
        b.trees.append({"id": tid, "title": t["title"], "shelf": t["shelf"],
                        "root": key_to_id[root_key], "emergency": bool(t.get("emergency")),
                        "tags": _check_tags(t.get("tags"), where + " tags", voc, b)})
        for ph in t.get("phrases") or []:
            say, always = _phrase(ph)
            b.node_phrases.append((say, key_to_id[root_key], where, always, tid, True))
        _check_label(t["title"], where + " title", b)

        for k, n in t["nodes"].items():
            key = f"{tid}/{k}"
            w = f"{path.name}:{k}"
            n = n or {}
            label = n.get("label", "")
            _check_label(label, w, b)
            pids = []
            for c in n.get("cite") or []:
                for pid in _resolve_cite(c, sections, manuals, w, b):
                    if pid not in pids:
                        pids.append(pid)
            if not (n.get("cite") or n.get("no_match")):
                b.issues.append(Issue("error", w, "no citation: every node must show sourced text"))
            fig = n.get("figure")
            if fig and fig not in figures:
                b.issues.append(Issue("error", w, f"figure {fig!r} was not extracted"))
            elif fig and figures[fig].get("restricted"):
                b.issues.append(Issue("error", w, f"figure {fig!r} is marked copyrighted/used "
                                                  "with permission; it can't ship"))
            elif fig:
                b.figures_used.add(fig)

            choices = []
            for ch in n.get("choices") or []:
                tgt = f"{tid}/{ch.get('to')}"
                if tgt not in key_to_id:
                    b.issues.append(Issue("error", w, f"choice goes to unknown node {ch.get('to')!r}"))
                    continue
                kind = ch.get("kind", "next")
                if kind not in KINDS:
                    b.issues.append(Issue("error", w, f"unknown choice kind {kind!r}"))
                _check_label(ch.get("label", ""), w + " choice", b)
                choices.append({"label": ch.get("label", ""), "to": key_to_id[tgt], "kind": kind})
            kinds = {c["kind"] for c in choices}
            if kinds & {"kit", "improvised"} and "nothing" not in kinds:
                b.issues.append(Issue("error", w, "has kit/improvised branches but no 'nothing' "
                                                  "branch (must always end at the safest option)"))

            look = []
            for c in n.get("lookalikes") or []:
                for pid in _resolve_cite(c, sections, manuals, w + " lookalikes", b):
                    if pid not in look:
                        look.append(pid)
            if plants:
                blob = " ".join([label] + [b.passages[p].text for p in pids + look])
                if BANNED_PLANT.search(blob):
                    b.issues.append(Issue("error", w, "mushrooms/fungi are banned from the plant "
                                                      "shelf; narrow the excerpt"))
                if not choices and not (n.get("no_match") or (n.get("species") and look)):
                    b.issues.append(Issue("error", w, "plant leaf must be species (with "
                                                      "lookalikes) or no_match"))

            for ph in n.get("phrases") or []:
                say, always = _phrase(ph)
                b.node_phrases.append((say, key_to_id[key], w, always, tid, key == root_key))
            b.nodes.append({"id": key_to_id[key], "key": key, "tree": tid, "label": label,
                            "passages": pids, "lookalikes": look, "figure": fig,
                            "choices": choices, "species": bool(n.get("species")),
                            "no_match": bool(n.get("no_match")),
                            "emergency": bool(n.get("emergency")),
                            "tags": _check_tags(n.get("tags"), w + " tags", voc, b)})

        # reachability
        ids = {key_to_id[f"{tid}/{k}"] for k in t["nodes"]}
        seen, stack = set(), [key_to_id[root_key]]
        by_id = {n["id"]: n for n in b.nodes}
        while stack:
            x = stack.pop()
            if x in seen or x not in by_id:
                continue
            seen.add(x)
            stack += [c["to"] for c in by_id[x]["choices"]]
        for x in sorted(ids - seen):
            b.issues.append(Issue("warning", where, f"node {by_id[x]['key']} is unreachable from root"))

    b.nodes.sort(key=lambda n: n["id"])
    return b


def _phrase(ph) -> tuple[str, bool]:
    if isinstance(ph, dict):
        return str(ph.get("say", "")), bool(ph.get("always"))
    return str(ph), False


def _check_label(label: str, where: str, b: Built) -> None:
    if not isinstance(label, str):
        b.issues.append(Issue("error", where, f"label {label!r} is not text (quote it in YAML; "
                                              "a comma or yes/no unquoted breaks it)"))
    elif not label:
        b.issues.append(Issue("error", where, "empty label"))
    elif len(label) > LABEL_MAX:
        b.issues.append(Issue("error", where, f"label over {LABEL_MAX} chars; labels are "
                                              "navigation only, put content in a citation"))
    elif any(ord(c) > 126 for c in label):
        b.issues.append(Issue("error", where, "label must be plain ASCII"))
