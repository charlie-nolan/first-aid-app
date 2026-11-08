"""Build the intermediate the packer consumes (see docs/INTERMEDIATE.md).

    build/intermediate/
      manifest.json     build id + sources (id, title, edition, sha256, distribution)
      passages.jsonl    one verbatim passage per line, with citation
      trees.json        trees, dense-numbered nodes, choices
      phrases.json      cmd_id -> phrase -> node
      phrases.txt/.h    ESP-SR phrase list
      strings.json      fixed UI strings (911 screen, disclaimer, no-match)
      pictures/*.png    1-bit, trimmed, scaled; pictures.json describes them
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path

import yaml

from . import phrases as ph
from .extract import load_figures, load_sections
from .pictures import build_picture
from .sources import PERSONAL_EXTRACT, PERSONAL_TREES, load_manifest
from .trees import DEFAULT_CONTEXTS, Issue, build_trees, pretty_manual


def pic_id(fig_id: str) -> str:
    return fig_id.replace("/", "_")


def build(root: Path, out: Path | None = None, extract_dir: Path | None = None,
          tree_dir: Path | None = None, max_phrases: int = ph.MAX_PHRASES,
          personal: bool = False) -> tuple[Path, list[Issue]]:
    """personal: also take the gitignored sources.personal.yaml manuals (extracted under
    build/extract_personal) and content/config.personal.yaml. Off by default, so a normal
    build never sees them."""
    out = out or root / "build" / ("intermediate_personal" if personal else "intermediate")
    extract_dir = extract_dir or root / "build" / "extract"
    personal_dir = root / "build" / PERSONAL_EXTRACT
    tree_dir = tree_dir or root / "content" / "trees"
    if personal and (root / "content" / PERSONAL_TREES).is_dir():
        # trees citing personal books live in the gitignored content/trees_personal/
        tree_dir = [tree_dir, root / "content" / PERSONAL_TREES]
    manuals = {m.id: m.raw | {"sha256": m.sha256, "distribution": m.distribution}
               for m in load_manifest(root / "sources.yaml", personal)}
    dirs = sorted(p for p in extract_dir.glob("*") if p.is_dir())
    if personal:
        dirs += sorted(p for p in personal_dir.glob("*") if p.is_dir() and p.name in manuals)
    sections = load_sections(dirs)
    figures = load_figures(dirs)
    config = yaml.safe_load((root / "content" / "config.yaml").read_text()) \
        if (root / "content" / "config.yaml").exists() else {}
    strings = yaml.safe_load((root / "content" / "strings.yaml").read_text()) \
        if (root / "content" / "strings.yaml").exists() else {}
    pconf = root / "content" / "config.personal.yaml"
    if personal and pconf.exists():
        # library_prepend: {folder: [tree ids]} puts personal shelf manuals first in a folder
        extra = yaml.safe_load(pconf.read_text()) or {}
        groups = dict(config.get("library_groups") or {})
        for g, tids in (extra.get("library_prepend") or {}).items():
            groups[g] = list(tids) + [t for t in groups.get(g, []) if t not in tids]
        config = {**config, "library_groups": groups}

    contexts = dict(DEFAULT_CONTEXTS)
    if (root / "content" / "contexts.yaml").exists():
        contexts.update(yaml.safe_load((root / "content" / "contexts.yaml").read_text()) or {})
    b = build_trees(tree_dir, sections, figures, manuals, contexts)
    cmds, pissues = ph.build_commands(b.node_phrases, max_phrases=max_phrases)
    if config.get("reference_shelf"):
        from .shelf import add_reference_shelf
        by_manual = {}
        for mid in manuals:                       # sources.yaml order, file order inside
            p = (personal_dir if manuals[mid].get("personal") else extract_dir) / mid / "sections.json"
            if p.exists():
                by_manual[mid] = json.loads(p.read_text())
        add_reference_shelf(b, by_manual, manuals)
    # tree table order = Library order on the device (and the n in ABOUT#n)
    groups = config.get("library_groups") or {}
    group_of = {tid: g for g, tids in groups.items() for tid in tids}
    flat = [tid for tids in groups.values() for tid in tids] or (config.get("library_order") or [])
    order = {tid: i for i, tid in enumerate(flat)}
    ungrouped = [t["id"] for t in b.trees if groups and t["id"] not in group_of]
    if ungrouped:
        pissues.append(Issue("warning", "config.yaml", f"trees in no library folder: {ungrouped}"))
    unknown = set(order) - {t["id"] for t in b.trees}
    if unknown:
        pissues.append(Issue("warning", "config.yaml", f"library_order names unknown trees: "
                                                       f"{sorted(unknown)}"))
    b.trees.sort(key=lambda t: order.get(t["id"], len(order)))   # stable for the rest
    issues = b.issues + pissues

    sleep = config.get("sleep_picture")
    if sleep and sleep not in figures and not (root / str(sleep)).exists():
        issues.append(Issue("error", "config.yaml", f"sleep_picture {sleep!r} not found"))
    home = config.get("home_tree")
    if home and home not in {t["id"] for t in b.trees}:
        issues.append(Issue("error", "config.yaml", f"home_tree {home!r} is not a tree"))

    out.mkdir(parents=True, exist_ok=True)
    (out / "pictures").mkdir(exist_ok=True)

    used_sources = sorted({p.source for p in b.passages.values()})
    manifest = {
        "build_time": int(time.time()),
        "sources": [{"id": s, "title": manuals.get(s, {}).get("title"),
                     "edition": manuals.get(s, {}).get("edition"),
                     "sha256": manuals.get(s, {}).get("sha256"),
                     "distribution": manuals.get(s, {}).get("distribution")} for s in used_sources],
    }
    with (out / "passages.jsonl").open("w") as f:
        for p in sorted(b.passages.values(), key=lambda p: p.id):
            f.write(json.dumps(asdict(p)) + "\n")

    pics = []
    wanted = sorted(b.figures_used | ({sleep} if sleep in figures else set()))
    for fid in wanted:
        fg = figures[fid]
        if not Path(fg["path"]).exists():
            issues.append(Issue("error", fid, "figure PNG missing on disk"))
            continue
        dst = out / "pictures" / f"{pic_id(fid)}.png"
        pic = build_picture(fg["path"], str(dst),
                            px_per_pt=fg["dpi"] / 72 if fg.get("dpi") else None)
        m = manuals.get(fg["manual"], {})
        pics.append({"id": pic_id(fid), "figure": fid, "file": f"pictures/{dst.name}", "w": pic.w,
                     "h": pic.h, "pan": pic.pan, "caption": f"Figure {fg['num']}. {fg['title']}" if "/fig/" in fid else "",
                     "source": fg["manual"],
                     "citation": f"{pretty_manual(fg['manual'], manuals)}, " + (
                         f"figure {fg['num']}" if "/fig/" in fid else f"page {fg['page']}")})
    if sleep and sleep not in figures and (root / str(sleep)).exists():
        dst = out / "pictures" / "sleep.png"
        pic = build_picture(str(root / sleep), str(dst))
        pics.append({"id": "sleep", "figure": None, "file": "pictures/sleep.png", "w": pic.w,
                     "h": pic.h, "pan": pic.pan, "caption": "", "source": None, "citation": None})

    nodes = []
    for n in b.nodes:
        nodes.append({**{k: n[k] for k in ("id", "key", "tree", "label", "passages", "lookalikes",
                                           "choices", "species", "no_match", "emergency", "tags")},
                      "unreviewed": n.get("unreviewed", False),
                      "figure": pic_id(n["figure"]) if n["figure"] else None})
    trees = {"trees": b.trees, "nodes": nodes, "home_tree": home,
             "sleep_picture": ("sleep" if sleep and sleep not in figures else
                               pic_id(sleep) if sleep else None)}
    (out / "trees.json").write_text(json.dumps(trees, indent=1))
    (out / "phrases.json").write_text(json.dumps(
        [{k: v for k, v in c.__dict__.items() if k not in ("entry", "tree")} for c in cmds], indent=1))
    ph.write_phrases_txt(cmds, out / "phrases.txt")
    ph.write_phrases_h(cmds, out / "phrases.h")
    (out / "pictures.json").write_text(json.dumps(pics, indent=1))
    # catalog lines for the reference shelf, readable by name in the current cartridge
    strings = dict(strings or {})
    # ABOUT#<n>: n = the tree's index in trees.json, which is the cartridge tree table
    # order (firmware knows the index, not the id; cart_string names are <= 31 chars)
    for i, t in enumerate(b.trees):
        if t.get("about"):
            strings[f"ABOUT:{t['id']}"[:31]] = t["about"]
            strings[f"ABOUT#{i}"] = t["about"]
    for i, t in enumerate(b.trees):
        if t["id"] in group_of:
            t["group"] = group_of[t["id"]]
            strings[f"GROUP#{i}"] = group_of[t["id"]]
    (out / "strings.json").write_text(json.dumps(strings, indent=1))
    (out / "contexts.json").write_text(json.dumps(contexts, indent=1))

    digest = hashlib.sha256()
    for name in ("passages.jsonl", "trees.json", "phrases.json", "pictures.json", "strings.json"):
        digest.update((out / name).read_bytes())
    manifest["build_id"] = digest.hexdigest()[:16]
    manifest["counts"] = {"trees": len(b.trees), "nodes": len(nodes), "passages": len(b.passages),
                          "phrases": len(cmds), "pictures": len(pics)}
    manifest["errors"] = len([i for i in issues if i.level == "error"])
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return out, issues
