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


def test_no_gap_inside_negated_phrase():
    slots = {"breathing": {"no": ["no breathing", "not breathing"]}}
    assert fill("itchy face with no trouble breathing", slots) == {}
    assert fill("he is not breathing", slots) == {"breathing": "no"}


def test_value_found_through_plain_link_on_a_question_card():
    tree = {"root": "start", "nodes": {
        "start": {"slot": "animal", "choices": [{"label": "Snake", "to": "snake", "value": "snake"},
                                                {"label": "Spider, tick or animal", "to": "other"}]},
        "other": _q("animal", spider="spider", tick="tick", mammal="mammal"),
        "snake": {}, "spider": {}, "tick": {}, "mammal": {}}}
    smap = {"slots": {"animal": {"snake": ["snake"], "spider": ["spider"], "tick": ["tick"], "mammal": ["dog"]}}}
    assert walk(tree, smap, fill("a spider bit me", smap["slots"])) == ("spider", ["start", "other"], None)
    assert walk(tree, smap, fill("something bit me", smap["slots"])) == ("start", [], "animal")


def test_tie_asks_except_on_life_threat_blanks_and_later_clause_wins():
    slots = {"cold": {"mild": ["shaking"], "cold_stress": ["still walking"], "moderate": ["confused"]},
             "conscious": {"no": ["passed out"], "yes": ["woke up"]}}
    pr = {"cold": ["moderate", "mild", "cold_stress"], "conscious": ["no", "yes"]}
    f = fill("shaking a lot and confused", slots, pr, decisive={"conscious"})
    assert "cold" not in f                                   # mild vs moderate tie: ask
    assert fill("shaking a lot and confused", slots, pr)["cold"] == "moderate"   # old behaviour
    assert fill("he passed out but woke up", slots, pr, decisive={"conscious"})["conscious"] == "yes"
    assert fill("he woke up then passed out", slots, pr, decisive={"conscious"})["conscious"] == "no"


def test_stemmed_verbs_and_nbest_life_threat_vote():
    from vr.intake import fill_nbest
    smap = {"slots": {"breathing": {"no": ["stopped breathing", "not breathing"]},
                      "body_part": {"leg": ["leg"], "arm": ["arm"]}},
            "life_threat": [{"if": {"breathing": "no"}, "go": "wfa-cpr/cpr"}]}
    assert fill("she's stop breathing", smap["slots"]) == {"breathing": "no"}       # -ed folded
    assert fill_nbest(["she's top breeding", "she's stopped breathing"], smap) == {"breathing": "no"}
    assert fill_nbest(["hurt my arm", "hurt my leg"], smap) == {"body_part": "arm"}  # no non-safety adds


def test_turns_to_card_counts_answers():
    from vr.intake import turns_to_card, _ancestors, _questions, simulate
    anc = _ancestors(TREE, _questions(TREE, SMAP))
    assert turns_to_card(TREE, SMAP, "a raccoon bit my thigh", {"limb"}, anc) == 1
    assert turns_to_card(TREE, SMAP, "the dog got me", {"face"}, anc) == 2      # asks where, then face
    assert turns_to_card(TREE, SMAP, "something bit me", {"face"}, anc) == 3    # asks animal, then where
    assert turns_to_card(TREE, SMAP, "a snake bit me", {"face"}, anc) is None   # wrong card opened
    seeds = {"nodes": {"face": {"train": ["the dog got me", "something bit me"]}}}
    out = simulate(TREE, SMAP, seeds)
    assert "1: 0%  2: 50%  3: 100%" in out


def test_answer_turn_numbers_yes_no_words_and_extra_blanks():
    from vr.intake import answer_turn, turns_to_card, _ancestors, _questions, wordlists
    f, pick, cmd = answer_turn("number two", TREE, SMAP, "mammal", "where", {"animal": "mammal"})
    assert pick["to"] == "face" and f["where"] == "face" and cmd is None
    f, pick, _ = answer_turn("on his cheek and it's gushing", TREE, SMAP, "mammal", "where", {})
    assert pick["to"] == "face" and f["bleeding"] == "severe"          # second blank heard in the same breath
    assert answer_turn("go back", TREE, SMAP, "mammal", "where", {})[2] == "back"
    assert answer_turn("um maybe", TREE, SMAP, "mammal", "where", {})[1] is None
    yn = {"root": "q", "nodes": {"q": {"slot": "breathing", "choices": [
        {"label": "Yes", "to": "ok", "value": "yes"}, {"label": "No", "to": "cpr", "value": "no"}]},
        "ok": {}, "cpr": {}}}
    sm = {"slots": {"breathing": {"no": ["not breathing"]}}}
    assert answer_turn("nope", yn, sm, "q", "breathing", {})[1]["to"] == "cpr"
    assert answer_turn("yes no", yn, sm, "q", "breathing", {})[1] is None
    anc = _ancestors(TREE, _questions(TREE, SMAP))
    assert turns_to_card(TREE, SMAP, "something bit me", {"limb"}, anc, say="label") == 3
    wl = wordlists(TREE, SMAP)
    assert wl["mammal"]["slot"] == "where" and {"cheek", "thigh", "two", "back"} <= set(wl["mammal"]["words"])


def test_one_word_no_verb_fold_future_hedge_and_soundalike_guard():
    from vr.intake import fill_nbest
    slots = {"bleeding": {"severe": ["squirting", "won't stop bleeding"]},
             "conscious": {"no": ["passed out"]}, "breathing": {"no": ["stopped breathing"]}}
    assert fill("rinse it with a squirt bottle", slots) == {}
    assert fill("it's squirting", slots) == {"bleeding": "severe"}
    assert fill("i think she will pass out", slots) == {}
    assert fill("she passed out", slots) == {"conscious": "no"}
    smap = {"slots": slots, "life_threat": [{"if": {"breathing": "no"}, "go": "cpr"}]}
    assert fill_nbest(["how do i stop bleeding", "how do i stop breathing"], smap) == {}
    assert fill_nbest(["she's stop breath in", "she stopped breathing"], smap) == {"breathing": "no"}


def test_soundalike_life_threat_shows_both_tiles():
    from vr.intake import life_options
    smap = {"slots": {"breathing": {"no": ["stop breathing", "stopped breathing"]},
                      "bleeding": {"severe": ["won't stop bleeding", "stop bleeding"]}},
            "life_threat": [{"if": {"breathing": "no"}, "go": "wfa-cpr/cpr"},
                            {"if": {"bleeding": "severe"}, "go": "wfa-bleeding/severe"}]}
    t = {"root": "start", "nodes": {"start": {}}}
    assert life_options(["my leg wind stop breathing", "my leg won't stop bleeding"], t, smap) == \
        ["wfa-cpr/cpr", "wfa-bleeding/severe"]
    assert life_options(["she stopped breathing", "she stopped breathing now"], t, smap) == ["wfa-cpr/cpr"]
