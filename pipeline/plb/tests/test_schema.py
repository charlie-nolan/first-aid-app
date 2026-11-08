"""Schema checks on content files: these run without any manual or extract."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from plb.cli import main as plb_main
from plb.schema import validate_content, validate_tree

TREE = {
    "id": "demo", "title": "Demo", "shelf": "survival", "root": "start",
    "phrases": ["demo topic", {"say": "help demo", "always": True}],
    "tags": {"region": ["pnw", "-id"]},
    "nodes": {
        "start": {"label": "Have the thing?", "cite": ["FIX1-1/1-1"], "figure": "FIX1-1/fig/1-1",
                  "choices": [{"label": "Kit", "to": "kit", "kind": "kit"},
                              {"label": "Nothing", "to": "none", "kind": "nothing"}]},
        "kit": {"label": "Kit", "cite": [{"ref": "FIX1-1/1-2", "from": "START", "to": "END"}]},
        "none": {"label": "Nothing", "cite": ["FIX1-1/1-3"], "emergency": True},
    },
}


def errs(issues):
    return [str(i) for i in issues if i.level == "error"]


def test_good_tree_passes():
    assert validate_tree(TREE, "demo.yaml") == []


@pytest.mark.parametrize("mutate,expect", [
    (lambda t: t["nodes"]["kit"].update({"cites": t["nodes"]["kit"].pop("cite")}), "cites: unknown key"),
    (lambda t: t["nodes"]["start"].update({"choises": t["nodes"]["start"].pop("choices")}), "choises: unknown key"),
    (lambda t: t.update({"titel": "x"}), "titel: unknown key"),
    (lambda t: t.pop("title"), "title: Field required"),
    (lambda t: t.update({"shelf": "medicine"}), "shelf: Input should be"),
    (lambda t: t.update({"root": "nope"}), "root 'nope' is not a node"),
    (lambda t: t["nodes"]["start"]["choices"][0].update({"to": "gone"}), "unknown node 'gone'"),
    (lambda t: t["nodes"]["start"]["choices"][0].update({"kind": "spare"}), "kind: Input should be"),
    (lambda t: t["nodes"]["none"].pop("cite"), "no citation"),
    (lambda t: t["nodes"]["none"].update({"cite": ["ATP 4-02.11 para 3"]}), "should look like MANUAL/PARA"),
    (lambda t: t["nodes"]["kit"]["cite"][0].update({"form": "START"}), "form: unknown key"),
    (lambda t: t["nodes"]["start"].update({"figure": "fig 1-1"}), "figure 'fig 1-1' should look like"),
    (lambda t: t["nodes"]["none"].update({"emergency": "maybe"}), "emergency: Input should be a valid boolean"),
    (lambda t: t.update({"tags": ["wa"]}), "tags must be a map"),
    (lambda t: t.update({"tags": {"planet": ["mars"]}}), "unknown tag dimension"),
    (lambda t: t["phrases"].append({"say": "x", "allways": True}), "allways: unknown key"),
    (lambda t: t["nodes"]["none"].update({"species": True}), "needs `lookalikes:`"),
    (lambda t: t.update({"id": "Demo Tree"}), "must be lowercase"),
    (lambda t: t.update({"nodes": {}}), "nodes: Dictionary should have at least 1 item"),
])
def test_bad_tree_fails(mutate, expect):
    t = copy.deepcopy(TREE)
    mutate(t)
    e = errs(validate_tree(t, "demo.yaml"))
    assert any(expect in x for x in e), e


def write(root: Path, rel: str, data) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data if isinstance(data, str) else yaml.safe_dump(data))


def test_validate_content_files(tmp_path):
    c = tmp_path / "content"
    write(c, "trees/demo.yaml", TREE)
    write(c, "config.yaml", {"home_tree": "demo", "sleep_picture": None, "reference_shelf": True,
                             "library_groups": {"First aid": ["demo"]}})
    write(c, "contexts.yaml", {"season": ["spring", "summer"], "groups": {"pnw": ["wa", "or"]}})
    write(c, "strings.yaml", {"WAKE_911": "Life threat? Call 911 / SOS first."})
    write(c, "phrases/expect.yaml", {"demo topic": "demo/start"})
    assert errs(validate_content(c)) == []

    write(c, "config.yaml", {"home_tre": "demo"})
    write(c, "contexts.yaml", {"season": ["summer", "summer"]})
    write(c, "strings.yaml", {"WAKE_911": "Call 911 — now"})
    write(c, "phrases/expect.yaml", {"demo topic": "demo"})
    write(c, "trees/copy.yaml", TREE)
    write(c, "trees/broken.yaml", "id: [unclosed\n")
    e = errs(validate_content(c))
    for want in ("config.yaml home_tre: unknown key", "duplicate values ['summer']",
                 "plain ASCII", "should be tree/node", "also used by trees/copy.yaml",
                 "trees/broken.yaml: not valid YAML"):
        assert any(want in x for x in e), (want, e)


def test_cli_validate(tmp_path, capsys):
    write(tmp_path / "content", "trees/demo.yaml", TREE)
    assert plb_main(["--root", str(tmp_path), "validate"]) == 0
    bad = copy.deepcopy(TREE)
    bad["nodes"]["kit"]["cites"] = bad["nodes"]["kit"].pop("cite")
    write(tmp_path / "content", "trees/demo.yaml", bad)
    assert plb_main(["--root", str(tmp_path), "validate"]) == 1
    assert "cites: unknown key" in capsys.readouterr().out


def test_repo_content_is_valid():
    """The content committed in this repo must always pass the schema."""
    content = Path(__file__).resolve().parents[1] / "content"
    assert errs(validate_content(content)) == []
