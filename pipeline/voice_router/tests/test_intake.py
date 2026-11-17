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
