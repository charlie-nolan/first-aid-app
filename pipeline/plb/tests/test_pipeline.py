"""End-to-end test on a generated fixture manual.

The fixture uses neutral placeholder text on purpose: it exercises layout handling
(numbered paragraphs, sub-paragraphs, hyphenation, headers/footers, a figure) and
never pretends to be first-aid content.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from plb.build import build
from plb.check import run_checks
from plb.extract import extract
from plb.sources import ingest

FIX_ID = "FIX1-1"


def make_pdf(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)
    w, h = letter

    def header_footer(n):
        c.setFont("Helvetica", 8)
        c.drawString(72, h - 30, "FIX 1-1  1 January 2026")
        c.drawString(w / 2, 30, f"{n}")

    header_footer(1)
    y = h - 90
    c.setFont("Helvetica", 9)
    c.drawString(72, y, "DISTRIBUTION RESTRICTION: Approved for public release; distribution is unlimited.")
    y -= 30
    c.setFont("Helvetica-Bold", 14); c.drawString(72, y, "Chapter 1"); y -= 22
    c.setFont("Helvetica-Bold", 11); c.drawString(72, y, "PLACEHOLDER TOPIC"); y -= 20
    c.setFont("Helvetica", 10)
    for line in ["1-1. Alpha placeholder sentence one for the fixture. Alpha placeholder",
                 "sentence two continues across a line with a hyphen-",
                 "ated word at the break.",
                 "a. First lettered item of the placeholder paragraph.",
                 "b. Second lettered item of the placeholder paragraph.",
                 "1-2. Bravo placeholder paragraph that the tree will excerpt from. It has",
                 "a start marker START HERE and an end marker END HERE and more words."]:
        c.drawString(72, y, line); y -= 14
    # figure: some vector strokes, caption below
    y -= 10
    c.rect(150, y - 120, 300, 110)
    c.line(150, y - 120, 450, y - 10)
    c.circle(300, y - 65, 30)
    y -= 140
    c.drawString(72, y, "Figure 1-1. Placeholder diagram")
    c.showPage()
    header_footer(2)
    y = h - 90
    c.setFont("Helvetica", 10)
    for line in ["1-3. Charlie placeholder paragraph on the second page of the fixture.",
                 "\u2022 Bullet placeholder item.",
                 "1-4. Delta placeholder paragraph that mentions a mushroom on purpose."]:
        c.drawString(72, y, line); y -= 14
    c.showPage()
    c.save()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "content" / "trees").mkdir(parents=True)
    (root / "content" / "phrases").mkdir(parents=True)
    (root / "sources.yaml").write_text(yaml.safe_dump({"manuals": [{
        "id": FIX_ID, "cite_as": "FIX 1-1", "title": "Fixture", "edition": "2026",
        "distribution": "unverified", "urls": [], "file": "sources/fix.pdf", "sha256": None}]}))
    pdf = tmp_path / "fix.pdf"
    make_pdf(pdf)
    ingest(root, root / "sources.yaml", FIX_ID, pdf)
    extract(root / "sources" / "fix.pdf", FIX_ID, root / "build" / "extract" / FIX_ID)
    return root


GOOD_TREE = {
    "id": "demo", "title": "Demo", "shelf": "survival", "root": "start",
    "phrases": ["demo topic"],
    "nodes": {
        "start": {"label": "Have the thing?", "cite": [f"{FIX_ID}/1-1"],
                  "figure": f"{FIX_ID}/fig/1-1", "phrases": ["the thing"],
                  "choices": [{"label": "Kit", "to": "kit", "kind": "kit"},
                              {"label": "Improvise", "to": "imp", "kind": "improvised"},
                              {"label": "Nothing", "to": "none", "kind": "nothing"}]},
        "kit": {"label": "Kit", "cite": [{"ref": f"{FIX_ID}/1-2", "from": "START HERE",
                                          "to": "END HERE"}]},
        "imp": {"label": "Improvised", "cite": [f"{FIX_ID}/1-3"]},
        "none": {"label": "Nothing", "cite": [f"{FIX_ID}/1-1"]},
    },
}


def write_tree(root: Path, tree: dict, name="demo.yaml"):
    (root / "content" / "trees" / name).write_text(yaml.safe_dump(tree))


def errors(issues):
    return [str(i) for i in issues if i.level == "error"]


def test_ingest_pins_hash_and_reads_distribution(repo):
    m = yaml.safe_load((repo / "sources.yaml").read_text())["manuals"][0]
    assert len(m["sha256"]) == 64
    assert "approved for public release" in m["distribution"].lower()


def test_extract_sections(repo):
    secs = {s["para"]: s for s in json.loads(
        (repo / "build" / "extract" / FIX_ID / "sections.json").read_text())}
    assert set(secs) == {"1-1", "1-2", "1-3", "1-4"}
    t = secs["1-1"]["text"]
    assert "hyphenated word" in t                       # line-break hyphen re-joined
    assert "\na. First lettered item" in t              # sub-paragraph on its own line
    assert "FIX 1-1  1 January" not in t                # running header dropped
    assert secs["1-1"]["heading"] == "PLACEHOLDER TOPIC"
    assert secs["1-3"]["pages"] == [2]
    assert secs["1-3"]["text"].endswith("\n- Bullet placeholder item.")


def test_extract_figure(repo):
    figs = json.loads((repo / "build" / "extract" / FIX_ID / "figures.json").read_text())
    assert [f["num"] for f in figs] == ["1-1"]
    assert (repo / "build" / "extract" / FIX_ID / "figures" / figs[0]["png"]).exists()


def test_good_tree_builds_and_passes(repo):
    write_tree(repo, GOOD_TREE)
    (repo / "content" / "phrases" / "expect.yaml").write_text(
        yaml.safe_dump({"Demo topic": "demo/start", "the thing": "demo/start"}))
    out, issues = build(repo)
    issues = run_checks(repo, out, issues)
    assert errors(issues) == []
    nodes = json.loads((out / "trees.json").read_text())["nodes"]
    assert nodes[0]["key"] == "demo/start"             # root gets the tree's first id
    assert sorted(n["key"] for n in nodes) == ["demo/imp", "demo/kit", "demo/none", "demo/start"]
    passages = [json.loads(l) for l in (out / "passages.jsonl").read_text().splitlines()]
    ex = next(p for p in passages if "#" in p["id"])
    assert ex["text"].startswith("START HERE") and ex["text"].endswith("END HERE")
    assert ex["citation"] == "FIX 1-1, para 1-2"
    pics = json.loads((out / "pictures.json").read_text())
    assert pics[0]["w"] <= 250 and pics[0]["h"] <= 122
    assert (out / "phrases.txt").read_text() == "0\tdemo topic\n1\tthe thing\n"


@pytest.mark.parametrize("mutate,expect", [
    (lambda t: t["nodes"]["imp"].pop("cite"), "no citation"),
    (lambda t: t["nodes"]["start"]["choices"].pop(), "no 'nothing' branch"),
    (lambda t: t["nodes"]["kit"]["cite"][0].update({"from": "NOT IN TEXT"}), "not found verbatim"),
    (lambda t: t["nodes"]["imp"].update({"cite": [f"{FIX_ID}/9-9"]}), "not an extracted section"),
    (lambda t: t["nodes"]["imp"].update({"label": "x" * 41}), "label over"),
    (lambda t: t["nodes"]["imp"].update({"phrases": ["the thing"]}), "already opens node"),
    (lambda t: t["nodes"]["start"].update({"figure": f"{FIX_ID}/fig/9-9"}), "not extracted"),
])
def test_rule_violations_fail(repo, mutate, expect):
    import copy
    t = copy.deepcopy(GOOD_TREE)
    mutate(t)
    write_tree(repo, t)
    out, issues = build(repo)
    errs = errors(run_checks(repo, out, issues))
    assert any(expect in e for e in errs), errs


def test_plant_shelf_bans_mushrooms(repo):
    tree = {"id": "plants", "title": "Plants", "shelf": "plants", "root": "q",
            "nodes": {"q": {"label": "Leaf shape?", "cite": [f"{FIX_ID}/1-4"],
                            "species": True, "lookalikes": [f"{FIX_ID}/1-3"]}}}
    write_tree(repo, tree, "plants.yaml")
    out, issues = build(repo)
    assert any("mushrooms" in e for e in errors(issues))


def test_expect_mismatch_fails(repo):
    write_tree(repo, GOOD_TREE)
    (repo / "content" / "phrases" / "expect.yaml").write_text(
        yaml.safe_dump({"the thing": "demo/kit"}))
    out, issues = build(repo)
    assert any("expected demo/kit" in e for e in errors(run_checks(repo, out, issues)))


def test_tampered_source_fails(repo):
    write_tree(repo, GOOD_TREE)
    with (repo / "sources" / "fix.pdf").open("ab") as f:
        f.write(b"\n% tampered\n")
    out, issues = build(repo)
    assert any("changed since it was pinned" in e for e in errors(run_checks(repo, out, issues)))


def test_edited_extract_fails_verbatim_check(repo):
    write_tree(repo, GOOD_TREE)
    out, issues = build(repo)
    p = out / "passages.jsonl"
    p.write_text(p.read_text().replace("Charlie placeholder", "Invented advice"))
    assert any("not verbatim" in e for e in errors(run_checks(repo, out, issues)))


def test_tags_emergency_always_pass_through(repo):
    import copy
    t = copy.deepcopy(GOOD_TREE)
    t["tags"] = {"region": ["pnw", "-id"], "season": ["winter"]}
    t["nodes"]["imp"]["tags"] = {"activity": ["water"]}
    t["nodes"]["none"]["emergency"] = True
    t["nodes"]["none"]["phrases"] = [{"say": "help now", "always": True}]
    write_tree(repo, t)
    out, issues = build(repo)
    assert errors(run_checks(repo, out, issues)) == []
    tr = json.loads((out / "trees.json").read_text())
    assert tr["trees"][0]["tags"] == {"region": ["pnw", "-id"], "season": ["winter"]}
    nodes = {n["key"]: n for n in tr["nodes"]}
    assert nodes["demo/imp"]["tags"] == {"activity": ["water"]}
    assert nodes["demo/none"]["emergency"] is True and nodes["demo/kit"]["tags"] == {}
    ph = {p["phrase"]: p for p in json.loads((out / "phrases.json").read_text())}
    assert ph["help now"]["always"] is True and ph["the thing"]["always"] is False


@pytest.mark.parametrize("tags,expect", [
    ({"region": ["zz"]}, "unknown region tag"),
    ({"planet": ["mars"]}, "unknown tag dimension"),
    ({"season": ["monsoon"]}, "unknown season tag"),
    (["wa"], "tags must be a map"),
])
def test_bad_tags_fail(repo, tags, expect):
    import copy
    t = copy.deepcopy(GOOD_TREE)
    t["tags"] = tags
    write_tree(repo, t)
    out, issues = build(repo)
    assert any(expect in e for e in errors(issues)), errors(issues)


def test_custom_contexts_written(repo):
    (repo / "content" / "contexts.yaml").write_text(yaml.safe_dump(
        {"activity": ["hiking", "water", "vehicle", "camp", "hunting"], "groups": {"sw": ["az", "nm"]}}))
    import copy
    t = copy.deepcopy(GOOD_TREE)
    t["tags"] = {"region": ["sw"], "activity": ["hunting"]}
    write_tree(repo, t)
    out, issues = build(repo)
    assert errors(issues) == []
    assert json.loads((out / "contexts.json").read_text())["groups"] == {"sw": ["az", "nm"]}


def test_per_section_voice_budget(repo):
    import copy
    t = copy.deepcopy(GOOD_TREE)
    t["nodes"]["imp"]["phrases"] = [f"variant {chr(97 + i // 26)}{chr(97 + i % 26)}" for i in range(199)]
    write_tree(repo, t)
    out, issues = build(repo)
    assert any("voice list needs 201" in e for e in errors(issues)), errors(issues)
    t["nodes"]["imp"]["phrases"] = t["nodes"]["imp"]["phrases"][:150]
    other = copy.deepcopy(GOOD_TREE)
    other["id"] = "demo2"
    other["phrases"] = ["second topic"]
    for n in other["nodes"].values():
        n.pop("phrases", None)
    other["nodes"]["imp"]["phrases"] = [f"other {chr(97 + i // 26)}{chr(97 + i % 26)}" for i in range(150)]
    write_tree(repo, t)
    write_tree(repo, other, "demo2.yaml")
    out, issues = build(repo)                    # 300+ phrases total, but each list fits
    assert errors(issues) == []
    assert len(json.loads((out / "phrases.json").read_text())) > 300


def test_contexts_over_rom_bits_fail(repo):
    (repo / "content" / "contexts.yaml").write_text(yaml.safe_dump(
        {"season": ["spring", "summer", "fall", "winter", "monsoon"]}))
    write_tree(repo, GOOD_TREE)
    out, issues = build(repo)
    assert any("5 season values, the ROM has 4 bits" in e for e in errors(issues)), errors(issues)


def test_marker_adapter_maps_captions(repo, tmp_path):
    """Marker output layout: <root>/<pdf stem>/<stem>.md + _page_N_Figure_M.jpeg (0-based pages)."""
    from PIL import Image, ImageDraw

    from plb.marker import main as marker_main
    mroot = tmp_path / "marker_out"
    mdir = mroot / "fix"
    mdir.mkdir(parents=True)
    for name in ("_page_0_Figure_1.jpeg", "_page_1_Picture_2.jpeg", "_page_1_Figure_3.jpeg"):
        im = Image.new("RGB", (400, 200), "white")
        ImageDraw.Draw(im).rectangle([20, 20, 380, 180], outline="black", width=3)
        im.save(mdir / name)
    (mdir / "fix.md").write_text(
        "1-1. Alpha text.\n\n![](_page_0_Figure_1.jpeg)\n\n**Figure 1-1. Placeholder diagram**\n\n"
        "Some text.\n\n![](_page_1_Picture_2.jpeg)\n\nMore body text here.\n\n"
        "![](_page_1_Figure_3.jpeg)\n\n*Figure 2-4. Second diagram (C) 1999 Someone, used with permission*\n")
    out = repo / "build" / "extract" / FIX_ID
    marker_main([str(repo / "sources" / "fix.pdf"), str(out), "--marker-out", str(mroot)])
    figs = {f["id"]: f for f in json.loads((out / "figures.json").read_text())}
    assert set(figs) == {f"{FIX_ID}/fig/1-1", f"{FIX_ID}/img/p2-1", f"{FIX_ID}/fig/2-4"}
    assert figs[f"{FIX_ID}/fig/1-1"]["page"] == 1 and figs[f"{FIX_ID}/fig/1-1"]["title"] == "Placeholder diagram"
    assert figs[f"{FIX_ID}/fig/2-4"]["restricted"] is True
    assert figs[f"{FIX_ID}/fig/1-1"]["restricted"] is False
    # the build uses Marker's figure in place of plb's own crop
    import copy
    t = copy.deepcopy(GOOD_TREE)
    t["nodes"]["imp"]["figure"] = f"{FIX_ID}/img/p2-1"
    write_tree(repo, t)
    out2, issues = build(repo)
    assert errors(run_checks(repo, out2, issues)) == []
    pics = {p["figure"]: p for p in json.loads((out2 / "pictures.json").read_text())}
    assert pics[f"{FIX_ID}/img/p2-1"]["citation"] == "FIX 1-1, page 2"
    assert pics[f"{FIX_ID}/fig/1-1"]["w"] <= 250
    t["nodes"]["kit"]["figure"] = f"{FIX_ID}/fig/2-4"
    write_tree(repo, t)
    out3, issues = build(repo)
    assert any("used with permission" in e for e in errors(issues))


def test_notices_follow_their_paragraph(repo):
    p = repo / "build" / "extract" / FIX_ID / "sections.json"
    secs = json.loads(p.read_text())
    for s in secs:
        if s["para"] == "1-2":
            s["notices"] = [{"kind": "CAUTION", "text": "Placeholder caution box text."}]
    p.write_text(json.dumps(secs))
    write_tree(repo, GOOD_TREE)                      # kit node cites an excerpt of 1-2
    out, issues = build(repo)
    assert errors(run_checks(repo, out, issues)) == []
    nodes = {n["key"]: n for n in json.loads((out / "trees.json").read_text())["nodes"]}
    assert nodes["demo/kit"]["passages"][-1] == f"{FIX_ID}/1-2~caution1"
    ps = {json.loads(l)["id"]: json.loads(l) for l in (out / "passages.jsonl").read_text().splitlines()}
    assert ps[f"{FIX_ID}/1-2~caution1"]["text"] == "## CAUTION\nPlaceholder caution box text."
    # tampering with a notice is caught by the verbatim check
    pj = out / "passages.jsonl"
    pj.write_text(pj.read_text().replace("Placeholder caution box", "Made up"))
    assert any("not verbatim" in e for e in errors(run_checks(repo, out, issues)))


def test_reference_shelf(repo):
    (repo / "content" / "config.yaml").write_text("reference_shelf: true\n")
    write_tree(repo, GOOD_TREE)
    p = repo / "build" / "extract" / FIX_ID / "sections.json"
    secs = json.loads(p.read_text())
    secs[1]["notices"] = [{"kind": "WARNING", "text": "Placeholder warning."}]
    p.write_text(json.dumps(secs))
    out, issues = build(repo)
    assert errors(run_checks(repo, out, issues)) == []
    t = json.loads((out / "trees.json").read_text())
    ref = [x for x in t["trees"] if x["shelf"] == "reference"]
    assert len(ref) == 1 and ref[0]["unreviewed"] and not ref[0]["emergency"]
    assert not [x for x in t["trees"] if x["shelf"] != "reference"][0]["unreviewed"]
    nodes = {n["id"]: n for n in t["nodes"]}
    leaves = [n for n in nodes.values() if n["tree"] == ref[0]["id"] and n["passages"]]
    assert len(leaves) == len(secs) and all(n["unreviewed"] and not n["figure"] for n in leaves)
    assert any(pid.endswith("~warning1") for n in leaves for pid in n["passages"])
    ph = json.loads((out / "phrases.json").read_text())
    assert not any(nodes[c["node"]]["unreviewed"] for c in ph)


def test_reference_shelf_pages_long_lists(repo):
    from plb.shelf import add_reference_shelf
    from plb.trees import Built
    b = Built()
    secs = [{"id": f"M/1-{i}", "manual": "M", "para": f"1-{i}", "chapter": "Chapter 1",
             "heading": "", "text": f"t{i}"} for i in range(1, 101)]
    add_reference_shelf(b, {"M": secs}, {}, list_max=40)
    assert max(len(n["choices"]) for n in b.nodes) <= 40
    root = next(n for n in b.nodes if n["id"] == b.trees[0]["root"])
    assert [c["label"] for c in root["choices"]] == ["1-1 to 1-40", "1-41 to 1-80", "1-81 to 1-100"]


def test_reference_shelf_labels_distinct_and_titles_short(repo):
    from plb.shelf import TITLE_MAX, add_reference_shelf
    from plb.trees import Built
    b = Built()
    secs = [{"id": f"M/1-{i}", "manual": "M", "para": f"1-{i}", "chapter": "Chapter 1",
             "heading": "SAME HEADING", "text": f"Distinct opening words number {i} here."}
            for i in range(1, 5)]
    add_reference_shelf(b, {"M": secs}, {"M": {"cite_as": "A Very Long Manual Name That Will Not Fit"}})
    labels = [n["label"] for n in b.nodes if n["passages"]]
    assert len(set(labels)) == 4 and labels[0].startswith("1-1 Distinct opening")
    assert len(b.trees[0]["title"]) <= TITLE_MAX
    assert b.trees[0]["full_title"] == "A Very Long Manual Name That Will Not Fit"
