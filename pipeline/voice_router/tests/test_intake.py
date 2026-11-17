from vr.intake import fill, walk

TREE = {"root": "start", "nodes": {
    "start": {"label": "What bit you?"}, "snake": {"label": "Snake bite"},
    "mammal": {"label": "Where is the bite?"}, "limb": {"label": "Bite on an arm or leg"},
    "face": {"label": "Bite on the face"}}}
SMAP = {
    "life_threat": {"bleeding": {"severe": "wfa-bleeding/severe"}},
    "slots": {"animal": {"snake": ["snake", "rattler"], "mammal": ["dog", "raccoon", "bat"]},
              "where": {"limb": ["leg", "arm", "thigh"], "face": ["face", "cheek"]},
              "bleeding": {"severe": ["gushing", "spurting", "won't stop"]}},
    "questions": {"start": {"slot": "animal", "choices": {"snake": "snake", "mammal": "mammal"}},
                  "mammal": {"slot": "where", "choices": {"limb": "limb", "face": "face"}}}}


def run(text):
    return walk(TREE, SMAP, fill(text, SMAP["slots"]))


def test_skips_answered_questions():
    assert run("a raccoon bit my thigh") == ("limb", ["start", "mammal"], None)


def test_asks_only_the_missing_blank():
    assert run("something bit my leg") == ("start", [], "animal")
    assert run("the dog got me") == ("mammal", ["start"], "where")


def test_life_threat_first_and_negation():
    assert run("dog bit my leg and it's gushing") == ("wfa-bleeding/severe", [], None)
    assert fill("it's not gushing", SMAP["slots"]) == {}


def test_inline_slot_tags():
    tree = {"root": "start", "nodes": {
        "start": {"slot": "animal", "choices": [{"label": "Snake", "to": "snake", "value": "snake"},
                                                {"label": "Mammal", "to": "mammal", "value": "mammal"}]},
        "snake": {}, "mammal": {}}}
    smap = {"slots": SMAP["slots"]}
    assert walk(tree, smap, fill("rattler got him", smap["slots"])) == ("snake", ["start"], None)


def test_life_threat_rules_in_order():
    slots = {"conscious": {"no": ["unconscious", "passed out"]}, "breathing": {"yes": ["breathing"], "no": ["not breathing"]}}
    smap = {"slots": slots, "life_threat": [
        {"if": {"breathing": "no"}, "go": "cpr"},
        {"if": {"conscious": "no", "breathing": "yes"}, "go": "recovery"},
        {"if": {"conscious": "no"}, "go": "check_breathing"}]}
    t = {"root": "start", "nodes": {"start": {}}}
    go = lambda s: walk(t, smap, fill(s, slots))[0]
    assert go("he passed out but he's breathing") == "recovery"
    assert go("she's unconscious") == "check_breathing"
    assert go("he's unconscious and not breathing") == "cpr"


def test_simulate_scores_walks():
    from vr.intake import simulate
    tree = {"root": "start", "nodes": {
        "start": {"slot": "animal", "choices": [{"label": "Snake", "to": "snake", "value": "snake"},
                                                {"label": "Dog", "to": "dog", "value": "mammal"}]},
        "snake": {}, "dog": {}}}
    smap = {"slots": {"animal": {"snake": ["snake"], "mammal": ["dog"]}}}
    seeds = {"nodes": {"snake": {"train": ["a snake bit me"], "test": ["it bit me"]},
                       "dog": {"train": ["a snake got him"]}}}
    out = simulate(tree, smap, seeds)
    assert "3 lines" in out and "right 33%" in out and "on way 33%" in out and "WRONG 33%" in out


def _q(slot, **choices):
    return {"slot": slot, "choices": [{"label": v, "to": t, "value": v} for v, t in choices.items()]}


def test_value_implies_parent_answer():
    tree = {"root": "where", "nodes": {"where": _q("body_part", arm="arm", leg="leg"),
                                       "arm": _q("body_part", forearm="fa", upper_arm="ua"),
                                       "leg": {}, "fa": {}, "ua": {}}}
    smap = {"slots": {"body_part": {"forearm": ["wrist", "forearm"], "upper_arm": ["upper arm"]}},
            "implies": {"body_part": {"forearm": "arm", "upper_arm": "arm"}}}
    assert walk(tree, smap, fill("broke my wrist", smap["slots"])) == ("fa", ["where", "arm"], None)


def test_coverage_then_priority_breaks_ties():
    slots = {"wound": {"clean": ["cut", "gash"], "infect": ["pus", "red streak", "hot"]},
             "allergy": {"mild": ["hives"], "severe": ["hives everywhere", "puffy face"]}}
    assert fill("the cut is hot and oozing pus", slots)["wound"] == "infect"
    assert fill("a cut with pus", slots, {"wound": ["infect"]})["wound"] == "infect"
    assert fill("hives everywhere", slots)["allergy"] == "severe"


def test_hedge_fills_nothing():
    slots = {"breathing": {"yes": ["is breathing"], "no": ["not breathing"]}}
    assert fill("i can barely tell if she is breathing", slots) == {}


def test_unless_and_same_tree_jump_keeps_walking():
    tree = {"root": "start", "nodes": {"start": _q("bleeding_type", arterial="severe", minor="minor"),
                                       "severe": _q("body_part", limb="tq", junction="pack"),
                                       "minor": {}, "tq": {}, "pack": {}}}
    smap = {"slots": {"bleeding_type": {"arterial": ["spurting"], "minor": ["oozing"]},
                      "body_part": {"limb": ["leg", "arm"], "junction": ["groin"]},
                      "seizing": {"yes": ["jerking"]}, "conscious": {"no": ["unresponsive"]}},
            "life_threat": [{"if": {"conscious": "no"}, "unless": {"seizing": "yes"}, "go": "wfa-cpr/start"},
                            {"if": {"bleeding_type": "arterial"}, "go": "severe"}]}
    f = lambda t: walk(tree, smap, fill(t, smap["slots"]))
    assert f("blood spurting from his groin") == ("pack", ["severe"], None)
    assert f("he's unresponsive") == ("wfa-cpr/start", [], None)
    assert f("jerking and unresponsive")[0] == "start"


def test_passes_menu_only_toward_one_answered_blank():
    tree = {"root": "menu", "nodes": {
        "menu": {"choices": [{"label": "Bleeding", "to": "b"}, {"label": "Bones", "to": "s"}]},
        "b": _q("missing_kit", tourniquet="tq", dressing="dr"), "s": _q("missing_kit", splint="sp"),
        "tq": {}, "dr": {}, "sp": {}}}
    smap = {"slots": {"missing_kit": {"tourniquet": ["no tourniquet"], "splint": ["no splint"]}}}
    assert walk(tree, smap, fill("we have no tourniquet", smap["slots"])) == ("tq", ["menu", "b"], None)
    assert walk(tree, smap, fill("help", smap["slots"])) == ("menu", [], None)


def test_plurals_and_ly_match():
    slots = {"kit": {"blister": ["blister bandage"]}, "depth": {"deep": ["deep"]}}
    assert fill("we ran out of blister bandages", slots) == {"kit": "blister"}
    assert fill("she got cut deeply", slots) == {"depth": "deep"}


def test_one_filler_word_but_not_a_negation():
    slots = {"allergy": {"severe": ["throat tight"], "mild": ["rash"]}}
    assert fill("a rash and now her throat is tight", slots)["allergy"] == "severe"
    assert fill("a rash but her throat not tight", slots)["allergy"] == "mild"
