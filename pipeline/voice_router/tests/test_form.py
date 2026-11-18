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
    two = "a dog bit me and I have a big burn on my arm"
    assert first_sentence(two, [Candidate("bites", TREE, SMAP, p=0.5), Candidate("burns", BURNS, BSMAP, p=0.4)])[0] == "form"
    assert first_sentence(two, cands, multi=True)[0] == "tabs"     # TALK + WIN_R held
    tabs = split_tabs(cands)
    assert [[c.name for c in t] for t in tabs] == [["bites"], ["burns"]]
    assert answer("on the leg", tabs[0]) == ("open", "bites", "limb")


def test_sector_redecode_per_candidate():
    c = Candidate("bites", TREE, SMAP, hyps=["a raccoon bit my thigh"])
    assert first_sentence("a rack coon bit my thy", [c]) == ("open", "bites", "limb")


def test_madlibs_snap_near_word_but_not_on_life_blanks():
    cands = [Candidate("bites", TREE, SMAP)]
    first_sentence("a dog got me", cands)
    assert answer("on the thigg", cands) == ("open", "bites", "limb")   # "thigg" -> thigh -> limb
    cands = [Candidate("bites", TREE, SMAP)]
    first_sentence("a dog got me", cands)
    assert answer("purple", cands)[0] == "form"                      # nothing close: ask again


def test_skip_lists_the_cards_still_possible():
    from vr.form import skip
    cands = [Candidate("bites", TREE, SMAP)]
    first_sentence("a dog got me", cands)
    assert skip(cands) == [("bites", "limb"), ("bites", "face")]


def test_saying_skip_lists_cards():
    cands = [Candidate("bites", TREE, SMAP)]
    first_sentence("a dog got me", cands)
    assert answer("skip", cands) == ("skip", None, [("bites", "limb"), ("bites", "face")])


def test_partial_words_rank_the_likely_card_first():
    from vr.form import ranked_cards
    slots = {**SMAP["slots"], "where": {"limb": ["lower leg", "upper arm"], "face": ["face", "cheek"]}}
    smap = {**SMAP, "slots": slots}
    c = Candidate("bites", TREE, smap)
    first_sentence("a dog got my lower part", [c])          # "lower" alone: not enough to fill
    assert c.asking == "where"
    assert ranked_cards([c])[0] == ("bites", "limb")


def test_tapped_pick_fills_one_field():
    from vr.form import pick
    cands = [Candidate("bites", TREE, SMAP)]
    first_sentence("a dog got me", cands)
    assert pick("where", "face", cands) == ("open", "bites", "face")


def test_tapped_plain_tile_moves_to_its_card():
    from vr.form import pick
    nodes = {**TREE["nodes"], "mammal": {**TREE["nodes"]["mammal"], "choices": [
        {"label": "Not sure", "to": "other"}]}, "other": {"label": "Other bites"}}
    cands = [Candidate("bites", {**TREE, "nodes": nodes}, SMAP)]
    first_sentence("a dog got me", cands)
    assert pick("where", None, cands, to="other") == ("open", "bites", "other")


def test_an_answer_always_moves_the_form_on():
    from vr.form import pick
    nodes = {**TREE["nodes"], "mammal": {**TREE["nodes"]["mammal"], "slot": "where", "choices": [
        {"label": "Leg", "to": "limb", "value": "leg"}, {"to": "face", "value": "face"}]}}
    qs = {**SMAP["questions"], "mammal": {"slot": "where", "choices": {"arm": "limb", "face": "face"}}}
    cands = [Candidate("bites", {**TREE, "nodes": nodes}, {**SMAP, "questions": qs})]
    first_sentence("a dog got me", cands)
    assert [f.choices[1]["label"] for f in open_fields(cands)] == ["face"]     # no None labels
    assert pick("where", "leg", cands) == ("open", "bites", "limb")            # "leg" not in the map


def test_answer_to_another_trees_card_opens_it():
    from vr.form import fuzz, pick
    nodes = {**TREE["nodes"], "mammal": {**TREE["nodes"]["mammal"], "slot": "kit", "choices": [
        {"label": "No splint", "to": "gear/splint", "value": "splint"}]}}
    qs = {"start": SMAP["questions"]["start"], "mammal": {"slot": "kit", "choices": {"splint": "gear/splint"}}}
    tree, smap = {**TREE, "nodes": nodes}, {**SMAP, "questions": qs, "slots": {**SMAP["slots"], "kit": {"splint": ["splint"]}}}
    cands = [Candidate("bites", tree, smap)]
    first_sentence("a dog got me", cands)
    assert pick("kit", "splint", cands) == ("open", "bites", "gear/splint")
    assert fuzz({"bites": (tree, smap), "x": (TREE, SMAP)}, ["a dog got me", "snake"]) == []


def test_plain_tile_into_another_tree_opens_it_and_node_ids():
    from vr.form import node_id, pick
    nodes = {**TREE["nodes"], "mammal": {**TREE["nodes"]["mammal"], "choices": [
        {"label": "Start CPR", "to": "wfa-cpr/cpr"}]}}
    tree = {**TREE, "nodes": nodes}
    cands = [Candidate("bites", tree, SMAP)]
    first_sentence("a dog got me", cands)
    assert pick("where", None, cands, to="wfa-cpr/cpr") == ("open", "bites", "wfa-cpr/cpr")
    assert node_id("bites", "wfa-cpr/cpr", tree) == "wfa-cpr/cpr"
    assert node_id("bites", "face", tree) == "bites/face"
    cands = [Candidate("bites", tree, SMAP)]
    first_sentence("a dog got me", cands)
    assert pick("where", "face", cands) == ("open", "bites", "face")


def test_lookahead_fields_show_answers_only():
    nodes = {**TREE["nodes"], "mammal": {**TREE["nodes"]["mammal"], "choices": [
        {"label": "When to evacuate", "to": "face"}]}}
    cands = [Candidate("bites", {**TREE, "nodes": nodes}, SMAP)]
    first_sentence("something got me", cands)
    later = [f for f in open_fields(cands) if f.when]
    assert later and all(c.get("value") for f in later for c in f.choices)


def test_life_words_in_a_later_answer_open_the_life_card():
    cands = [Candidate("bites", TREE, SMAP)]
    first_sentence("a dog got me", cands)
    assert cands[0].asking == "where"
    assert answer("it won't stop, blood gushing everywhere", cands) == ("open", "bites", "wfa-bleeding/severe")


def test_bare_no_answers_one_field_only():
    yn = lambda slot, y, n: {"slot": slot, "choices": [{"label": "Yes", "to": y, "value": "yes"},
                                                       {"label": "No", "to": n, "value": "no"}]}
    tree = {"root": "q1", "title": "Knee", "nodes": {
        "q1": {"label": "Out of place?", **yn("dislocated", "d", "q2")},
        "q2": {"label": "Have a splint?", **yn("splint", "s", "nos")},
        "d": {"label": "D"}, "s": {"label": "S"}, "nos": {"label": "No splint"}}}
    smap = {"slots": {"knee": {"yes": ["knee"]}}}
    cands = [Candidate("knee", tree, smap)]
    first_sentence("my knee", cands)
    assert answer("no", cands) == ("form", None, None)
    assert cands[0].card == "q2" and "splint" not in cands[0].filled


def test_negated_word_never_snaps():
    cands = [Candidate("bites", TREE, SMAP)]
    first_sentence("a dog got me", cands)
    assert answer("not on the face", cands) == ("form", None, None)
    assert answer("maybe the face", cands) == ("form", None, None)


def test_tied_words_still_keep_their_tree():
    from vr.live import keep_trees
    frac = ({"root": "where", "nodes": {"where": {"label": "Where?"}, "leg": {}, "shin": {}}},
            {"slots": {"body_part": {"leg": ["leg"], "lower_leg": ["shin"]}},
             "questions": {"where": {"slot": "body_part", "choices": {"leg": "leg", "lower_leg": "shin"}}}})
    bleed = ({"root": "s", "nodes": {"s": {"label": "Bleeding?"}, "a": {}}},
             {"slots": {"bleeding": {"some": ["sticking"]}},
              "questions": {"s": {"slot": "bleeding", "choices": {"some": "a"}}}})
    kept = keep_trees(["my friend fell and broke is leg is bone a sticking at two the shin"],
                      {"fractures": frac, "wfa-bleeding": bleed})
    assert "fractures" in [c.name for c in kept]
