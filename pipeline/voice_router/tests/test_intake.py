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
