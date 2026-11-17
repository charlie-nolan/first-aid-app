from vr.form import Candidate, answer, first_sentence, form_turns, open_fields
from vr.intake import _ancestors, _questions

TREE = {"root": "start", "title": "Bites", "nodes": {
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
BURNS = {"root": "start", "title": "Burns", "nodes": {"start": {"label": "How big?"},
         "small": {"label": "Small burn"}, "big": {"label": "Big burn"}}}
BSMAP = {"slots": {"size": {"small": ["small"], "big": ["big", "large"]},
                   "where": {"limb": ["leg", "arm"], "face": ["face"]}},
         "questions": {"start": {"slot": "size", "choices": {"small": "small", "big": "big"}}}}


def test_life_threat_opens_on_first_sentence():
    cands = [Candidate("bites", TREE, SMAP)]
    assert first_sentence("the dog bite is gushing", cands) == ("open", "bites", "wfa-bleeding/severe")


def test_one_screen_answers_every_blank():
    cands = [Candidate("bites", TREE, SMAP)]
    assert first_sentence("something bit me", cands)[0] == "form"
    fs = open_fields(cands)
    assert [(f.key, f.when) for f in fs] == [("animal", None), ("where", ("animal", "mammal"))]
    assert answer("a dog, on my leg", cands) == ("open", "bites", "limb")


def test_which_is_it_field_and_shared_blanks():
    cands = [Candidate("bites", TREE, SMAP, p=0.5), Candidate("burns", BURNS, BSMAP, p=0.4)]
    assert first_sentence("my arm hurts", cands)[0] == "form"
    keys = [f.key for f in open_fields(cands)]
    assert keys[0] == "@topic" and set(keys[1:]) == {"animal", "size"}
    assert answer("burns, a big one", cands) == ("open", "burns", "big")


def test_form_turns_scores_one_screen():
    anc = _ancestors(TREE, _questions(TREE, SMAP))
    assert form_turns("bites", TREE, SMAP, "a raccoon bit my thigh", {"limb"}, anc) == 1
    assert form_turns("bites", TREE, SMAP, "something bit me", {"face"}, anc) == 2


def test_life_card_with_its_own_blank_opens_first():
    tree = {"root": "start", "title": "CPR", "nodes": {"start": {"label": "Breathing?"},
            "cpr": {"label": "Start CPR"}, "cold": {"label": "CPR when very cold"}}}
    smap = {"life_threat": [{"if": {"breathing": "no"}, "go": "cpr"}],
            "slots": {"breathing": {"no": ["not breathing"]}, "cold": {"yes": ["freezing", "from the water"]}},
            "questions": {"cpr": {"slot": "cold", "choices": {"yes": "cold"}}}}
    cands = [Candidate("cpr", tree, smap)]
    assert first_sentence("she's not breathing", cands) == ("open", "cpr", "cpr")
    assert [f.key for f in open_fields(cands)] == ["cold"]


def test_reaching_a_life_card_by_answering_the_root_does_not_open_it():
    tree = {"root": "start", "title": "CPR", "nodes": {"start": {"label": "Breathing?"},
            "cpr": {"label": "Start CPR"}, "recovery": {"label": "Recovery position"}}}
    smap = {"life_threat": [{"if": {"breathing": "no"}, "go": "cpr"},
                            {"if": {"conscious": "no", "breathing": "yes"}, "go": "recovery"}],
            "slots": {"breathing": {"no": ["not breathing"], "yes": ["breathing fine"]},
                      "conscious": {"no": ["unconscious"]}},
            "questions": {"start": {"slot": "breathing", "choices": {"no": "cpr", "yes": "recovery"}}}}
    cands = [Candidate("cpr", tree, smap, p=0.5), Candidate("cpr2", tree, smap, p=0.4)]
    assert first_sentence("he fell but he's breathing fine", cands)[0] == "form"
    assert first_sentence("he's unconscious but breathing fine", [Candidate("cpr", tree, smap)]) == ("open", "cpr", "recovery")


def test_two_problems_open_two_tabs():
    from vr.form import split_tabs
    cands = [Candidate("bites", TREE, SMAP, p=0.5), Candidate("burns", BURNS, BSMAP, p=0.4)]
    assert first_sentence("a dog bit me and I have a big burn on my arm", cands)[0] == "tabs"
    tabs = split_tabs(cands)
    assert [[c.name for c in t] for t in tabs] == [["bites"], ["burns"]]
    assert answer("on the leg", tabs[0]) == ("open", "bites", "limb")
