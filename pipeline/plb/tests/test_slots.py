"""plb slots: voice slot wiring checks and the slot map."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

from plb.cli import main as plb_main
from plb.slots import check_voice, collect, write_map

TREE = {
    "id": "demo", "title": "Demo", "shelf": "first-aid", "root": "start",
    "nodes": {
        "start": {"label": "Breathing?", "slot": "breathing", "cite": ["FIX1-1/1-1"],
                  "choices": [{"label": "No", "to": "cpr", "value": "no"},
                              {"label": "Yes", "to": "ok", "value": "yes"},
                              {"label": "When to evacuate", "to": "evac"}]},
        "cpr": {"label": "CPR", "cite": ["FIX1-1/1-2"]},
        "ok": {"label": "OK", "cite": ["FIX1-1/1-3"]},
        "evac": {"label": "Evac", "cite": ["FIX1-1/1-3"]},
    },
}
RULES = {"life_threat": [{"if": {"breathing": "no"}, "go": "demo/cpr"}]}


def setup(tmp: Path, tree=TREE, rules=RULES) -> tuple[Path, Path]:
    d = tmp / "trees"
    d.mkdir(exist_ok=True)
    (d / "demo.yaml").write_text(yaml.safe_dump(tree))
    r = tmp / "rules.yaml"
    r.write_text(yaml.safe_dump(rules))
    return d, r


def errors(rep):
    return [str(i) for i in rep.issues if i.level == "error"]


def test_good_wiring_and_map(tmp_path):
    d, r = setup(tmp_path)
    rep = collect([d], r)
    assert errors(rep) == []
    assert rep.vocab == {"breathing": {"no", "yes"}}
    text = write_map(rep, tmp_path / "map.md").read_text()
    assert "`demo/start` **breathing**: no -> demo/cpr, yes -> demo/ok  (navigation: 1)" in text
    assert "go: demo/cpr" in text


def test_duplicate_value_on_a_card(tmp_path):
    t = copy.deepcopy(TREE)
    t["nodes"]["start"]["choices"][1]["value"] = "no"
    d, r = setup(tmp_path, t)
    assert any("is on two answers" in e for e in errors(collect([d], r)))


def test_unreachable_card(tmp_path):
    t = copy.deepcopy(TREE)
    t["nodes"]["orphan"] = {"label": "Orphan", "cite": ["FIX1-1/1-3"]}
    d, r = setup(tmp_path, t)
    assert any("demo/orphan" in e and "can't be reached" in e for e in errors(collect([d], r)))


def test_bad_life_threat_rules(tmp_path):
    rules = {"life_threat": [{"if": {"breathing": "maybe"}, "go": "demo/cpr"},
                             {"if": {"pulse": "no"}, "go": "demo/cpr"},
                             {"if": {"breathing": "no"}, "go": "demo/gone"}]}
    d, r = setup(tmp_path, rules=rules)
    e = errors(collect([d], r))
    assert any("breathing='maybe' is not a value" in x for x in e)
    assert any("slot 'pulse' is not on any card" in x for x in e)
    assert any("'demo/gone' is not a card" in x for x in e)


def test_voice_slot_files(tmp_path):
    d, r = setup(tmp_path)
    v = tmp_path / "voice"
    v.mkdir()
    (v / "demo.yaml").write_text(yaml.safe_dump({
        "tree": "demo",
        "slots": {"breathing": {"no": ["Not Breathing"]}},
        "life_threat": [{"if": {"breathing": "no"}, "go": "nowhere"}]}))
    rep = collect([d], r)
    assert check_voice(rep, [d], v) == 1
    e = errors(rep)
    assert any("breathing=yes has no phrases" in x for x in e)
    assert any("'Not Breathing' is not lowercase" in x for x in e)
    assert any("go 'nowhere' is not a card" in x for x in e)


def test_cli_slots_fails_on_error(tmp_path, capsys):
    root = tmp_path / "repo"
    (root / "content" / "trees").mkdir(parents=True)
    t = copy.deepcopy(TREE)
    t["nodes"]["start"]["choices"][0]["to"] = "nowhere"
    (root / "content" / "trees" / "demo.yaml").write_text(yaml.safe_dump(t))
    (root / "sources.yaml").write_text("manuals: []\n")
    rc = plb_main(["--root", str(root), "slots"])
    assert rc == 1
    assert "unknown card 'nowhere'" in capsys.readouterr().out
