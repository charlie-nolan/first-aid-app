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
    assert [[c.name for c in t] for t in tabs] == [["burns"], ["bites"]]   # burns is an urgent tree (11-20)
    assert answer("on the leg", tabs[1]) == ("open", "bites", "limb")


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


def test_in_tree_life_rule_opens_the_card_it_names():
    tree = {"root": "s", "title": "Bleeding", "nodes": {"s": {"label": "Bleeding?"},
            "severe": {"label": "Stop the bleeding"}, "tq": {"label": "Apply a tourniquet"}}}
    smap = {"life_threat": [{"if": {"bleeding_type": "heavy"}, "go": "severe"}],
            "slots": {"bleeding_type": {"heavy": ["will not stop"]}, "body_part": {"limb": ["leg"]}},
            "questions": {"severe": {"slot": "body_part", "choices": {"limb": "tq"}}}}
    cands = [Candidate("bleed", tree, smap)]
    assert first_sentence("cut his leg and the bleeding will not stop", cands) == ("open", "bleed", "severe")


def test_repeating_a_heard_answer_on_the_life_card_moves_on():
    tree = {"root": "s", "title": "Bleeding", "nodes": {"s": {"label": "Bleeding?"},
            "severe": {"label": "Stop the bleeding"}, "tq": {"label": "Apply a tourniquet"}}}
    smap = {"life_threat": [{"if": {"bleeding_type": "heavy"}, "go": "severe"}],
            "slots": {"bleeding_type": {"heavy": ["bleeding a lot"]}, "body_part": {"limb": ["leg"]}},
            "questions": {"severe": {"slot": "body_part", "choices": {"limb": "tq"}}}}
    cands = [Candidate("bleed", tree, smap)]
    assert first_sentence("bleeding a lot from his leg", cands) == ("open", "bleed", "severe")
    assert answer("it's his leg", cands) == ("open", "bleed", "tq")


def _s007_pool():
    burns = ({"root": "s", "title": "Burns", "nodes": {"s": {"label": "How bad?"}, "a": {}, "b": {}}},
             {"slots": {"dirty": {"yes": ["dirty"], "no": ["clean"]}},
              "questions": {"s": {"slot": "dirty", "choices": {"yes": "a", "no": "b"}}}})
    wounds = ({"root": "s", "title": "Wounds", "nodes": {"s": {"label": "Dirty?"}, "a": {}, "b": {}}},
              {"slots": {"wound_risk": {"high": ["dirty"], "low": ["clean"]}},
               "questions": {"s": {"slot": "wound_risk", "choices": {"high": "a", "low": "b"}}}})
    choking = ({"root": "s", "title": "Choking", "nodes": {"s": {"label": "Can they cough?"},
                "cough": {"label": "Let them cough"}, "thrust": {"label": "Abdominal thrusts"},
                "out": {"label": "Unresponsive: start CPR"}}},
               {"life_threat": [{"if": {"conscious": "no"}, "go": "out"}],
                "slots": {"can_cough": {"yes": ["coughing"], "no": ["can't breathe", "can't cough"]},
                          "conscious": {"no": ["unresponsive", "passed out"]}},
                "questions": {"s": {"slot": "can_cough", "choices": {"yes": "cough", "no": "thrust"}}}})
    return {"burns": burns, "wounds": wounds, "wfa-choking": choking}


def test_s007_named_tree_is_kept_on_a_misheard_sentence():
    from vr.live import keep_trees
    kept = keep_trees(["eye think my dad is choking on dirty"], _s007_pool())
    assert kept[0].name == "wfa-choking"


def test_s007_choking_mid_form_switches_trees():
    from vr.live import keep_trees, rescue
    pool = _s007_pool()
    cands = [c for c in keep_trees(["my dad's arm is dirty"], pool) if c.name != "wfa-choking"]
    first_sentence("my dad's arm is dirty", cands)
    r = rescue(["choking in can't breathe"], pool, cands)
    assert r == ("open", "wfa-choking", "thrust")
    cands = [c for c in keep_trees(["my dad's arm is dirty"], pool) if c.name != "wfa-choking"]
    first_sentence("my dad's arm is dirty", cands)
    assert rescue(["he's unresponsive now"], pool, cands) == ("open", "wfa-choking", "out")
    cands = [c for c in keep_trees(["my dad's arm is dirty"], pool) if c.name != "wfa-choking"]
    first_sentence("my dad's arm is dirty", cands)
    assert rescue(["clean"], pool, cands) is None                     # a plain answer stays an answer


def test_s007_kept_tree_reads_the_words_before_other_trees():
    from vr.live import keep_trees, rescue
    pool = _s007_pool()
    pool["wfa-allergy"] = ({"root": "s", "title": "Allergic reaction", "nodes": {"s": {"label": "?"},
                            "severe": {"label": "Severe reaction"}}},
                           {"life_threat": [{"if": {"breathing": "hard"}, "go": "severe"}],
                            "slots": {"breathing": {"hard": ["can't breathe"]}}})
    cands = keep_trees(["choking"], pool)
    assert [c.name for c in cands] == ["wfa-choking"]
    first_sentence("choking", cands)
    assert rescue(["can't breathe"], pool, cands) is None             # choking's own form reads it
    assert answer("can't breathe", cands) == ("open", "wfa-choking", "thrust")


def _s010_pool():
    pool = _s007_pool()
    pool["wfa-co"] = ({"root": "s", "title": "Carbon monoxide", "nodes": {"s": {"label": "Awake?"},
                       "treat_now": {"label": "Get them to fresh air now"}, "mild": {"label": "Mild"}}},
                      {"life_threat": [{"if": {"conscious": "no"}, "go": "treat_now"}],
                       "slots": {"conscious": {"no": ["passed out"], "yes": ["awake"]},
                                 "source": {"stove": ["stove", "heater"]}},
                       "questions": {"s": {"slot": "conscious", "choices": {"no": "treat_now", "yes": "mild"}}}})
    pool["wfa-poison-ivy"] = ({"root": "s", "title": "Poison ivy, oak and sumac", "nodes": {"s": {}}},
                              {"slots": {"rash": {"yes": ["rash", "itchy"]}}})
    return pool


def test_s010_wont_wake_opens_the_co_card():
    from vr.live import keep_trees
    pool = _s010_pool()
    line = "carbon monoxide poisoning from the tent stove and my partner won't wake up"
    cands = keep_trees([line], pool)
    assert "wfa-poison-ivy" not in [c.name for c in cands]
    assert first_sentence(line, cands) == ("open", "wfa-co", "treat_now")


def test_s009_choking_and_unresponsive_opens_the_life_card():
    from vr.live import keep_trees
    pool = _s007_pool()
    cands = keep_trees(["choking unresponsive"], pool)
    assert first_sentence("choking unresponsive", cands) == ("open", "wfa-choking", "out")


def test_s007_replay_misheard_then_choking_then_cant_breathe():
    from vr.live import keep_trees, rescue
    pool = _s010_pool()
    cands = keep_trees(["eye think my dad is choking on dirty"], pool)
    assert cands[0].name == "wfa-choking"
    first_sentence("eye think my dad is choking on dirty", cands)
    r = rescue(["choking in can't breathe"], pool, cands) or answer("choking in can't breathe", cands)
    assert r == ("open", "wfa-choking", "thrust")


def test_named_condition_outranks_shared_words():
    from vr.live import keep_trees
    pool = _s010_pool()
    pool["burns"][1]["slots"].update({"cause": {"stove": ["stove", "tent stove"]},
                                      "where": {"hand": ["hand"]}, "deep": {"yes": ["blister"]}})
    kept = keep_trees(["carbon monoxide from the tent stove, his hand has a blister"], pool)
    assert kept[0].name == "wfa-co"


def test_top_ranked_tree_emergency_beats_a_lower_trees_jump():
    from vr.live import keep_trees
    pool = _s007_pool()
    pool["wfa-asthma"] = ({"root": "s", "title": "Asthma", "nodes": {"s": {"label": "How bad?"}, "m": {}}},
                          {"slots": {"severity": {"severe": ["can't breathe"], "mild": ["wheezy"]}},
                           "questions": {"s": {"slot": "severity",
                                               "choices": {"severe": "wfa-allergy/severe", "mild": "m"}}}})
    choking = pool["wfa-choking"][1]
    choking["slots"]["airway"] = {"blocked": ["can't breathe", "can't talk"]}
    choking["life_threat"] = [{"if": {"conscious": "no"}, "go": "out"}, {"if": {"airway": "blocked"}, "go": "thrust"}]
    line = "my dad is choking and can't breathe"
    cands = keep_trees([line], pool)
    assert cands[0].name == "wfa-choking"
    assert first_sentence(line, cands) == ("open", "wfa-choking", "thrust")
    cands = keep_trees(["he was choking and now he's passed out"], pool)
    assert first_sentence("he was choking and now he's passed out", cands) == ("open", "wfa-choking", "out")


def test_s010_naming_one_kept_tree_picks_it():
    pool = _s010_pool()
    cands = [Candidate("wfa-co", *pool["wfa-co"], p=0.5), Candidate("burns", *pool["burns"], p=0.5)]
    first_sentence("the tent stove is dirty", cands)
    assert open_fields(cands)[0].key == "@topic"
    answer("carbon monoxide poisoning", cands)
    assert [c.name for c in cands] == ["wfa-co"]
    assert answer("she won't wake up", cands) == ("open", "wfa-co", "treat_now")


def test_tie_prefers_a_trees_own_rule_over_another_trees_jump():
    pool = _s007_pool()
    choking = pool["wfa-choking"][1]
    choking["slots"]["airway"] = {"blocked": ["can't breathe"]}
    choking["life_threat"] = [{"if": {"airway": "blocked"}, "go": "thrust"}]
    asthma = ({"root": "s", "title": "Asthma", "nodes": {"s": {"label": "How bad?"}, "m": {}}},
              {"slots": {"severity": {"severe": ["can't breathe"], "mild": ["wheezy"]}},
               "questions": {"s": {"slot": "severity", "choices": {"severe": "wfa-allergy/severe", "mild": "m"}}}})
    cands = [Candidate("wfa-asthma", *asthma, p=0.5), Candidate("wfa-choking", *pool["wfa-choking"], p=0.5)]
    assert first_sentence("can't breathe", cands) == ("open", "wfa-choking", "thrust")


def test_s009_named_tree_beats_a_kept_trees_generic_rule():
    from vr.live import rescue
    pool = _s007_pool()
    pool["wfa-head-spine"] = ({"root": "s", "title": "Head and spine", "nodes": {"s": {"label": "?"}, "a": {}}},
                              {"life_threat": [{"if": {"conscious": "no"}, "go": "wfa-cpr/start"}],
                               "slots": {"conscious": {"no": ["knocked out"]}, "where": {"neck": ["neck"]}},
                               "questions": {"s": {"slot": "where", "choices": {"neck": "a"}}}})
    cands = [Candidate("wfa-head-spine", *pool["wfa-head-spine"])]
    first_sentence("hurt his neck", cands)
    assert rescue(["choking unresponsive"], pool, cands) == ("open", "wfa-choking", "out")


def test_body_words_are_not_topic_words():
    from vr.form import topic_words
    assert topic_words("wfa-head-spine", {"title": "Head, neck and back"}, {}) == set()
    assert topic_words("bites", {"title": "Bites and stings"}, {}) == set()


def test_plain_not_breathing_opens_cpr_not_a_condition_tree():
    from vr.live import keep_trees
    pool = _s007_pool()
    pool["wfa-choking"][1]["slots"]["breathing"] = {"no": ["not breathing"]}
    pool["wfa-cpr"] = ({"root": "s", "title": "CPR", "nodes": {"s": {"label": "Breathing?"}, "cpr": {"label": "Start CPR"}}},
                       {"life_threat": [{"if": {"breathing": "no"}, "go": "cpr"}],
                        "slots": {"breathing": {"no": ["not breathing"]}}})
    cands = keep_trees(["he's not breathing"], pool)
    assert cands[0].name == "wfa-cpr"
    assert first_sentence("he's not breathing", cands) == ("open", "wfa-cpr", "cpr")
    cands = keep_trees(["he was choking and now he's not breathing"], pool)
    assert cands[0].name == "wfa-choking"                    # named condition still wins


def test_calm_breathing_awake_is_not_cpr_evidence():
    """38b1139 scene check: "breathing fine and talking normally" put wfa-cpr first in 18 calm scenes.
    Only the life values (not breathing, won't wake) rank the CPR tree."""
    from vr.live import keep_trees
    pool = _s007_pool()
    pool["wfa-cpr"] = ({"root": "s", "title": "CPR", "nodes": {"s": {"label": "Breathing?"}, "cpr": {"label": "Start CPR"}}},
                       {"life_threat": [{"if": {"breathing": "no"}, "go": "cpr"}],
                        "slots": {"breathing": {"no": ["not breathing"], "yes": ["breathing fine"]},
                                  "conscious": {"no": ["passed out"], "yes": ["awake"]}}})
    pool["stings"] = ({"root": "s", "title": "Stings", "nodes": {"s": {"label": "Where?"}, "hand": {"label": "Hand"}}},
                      {"slots": {"body_part": {"hand": ["hand"]}}})
    names = [c.name for c in keep_trees(["a bee stung her hand, she's breathing fine and awake"], pool)]
    assert "wfa-cpr" not in names
    assert keep_trees(["she passed out and is not breathing"], pool)[0].name == "wfa-cpr"


def test_stopped_then_she_is_breathing_is_not_stopped_breathing():
    """81fe3ed PC check: "the seizure just stopped. she's breathing" filled breathing=no (the "."
    is dropped, so "stopped [she's] breathing" matched) and opened CPR on a breathing person."""
    from vr.intake import fill
    slots = {"breathing": {"no": ["stopped breathing", "not breathing"], "yes": ["breathing"]}}
    for line in ["the seizure just stopped. she's breathing", "it stopped, he's breathing fine",
                 "the seizure stopped and she's breathing", "it stopped and now breathing"]:
        assert fill(line, slots).get("breathing") != "no", line
    assert fill("she stopped breathing", slots)["breathing"] == "no"


def test_cpr_cause_alone_and_place_alone_rank_nothing():
    """81fe3ed: CPR's own "cause" (hypothermia) ranked CPR first on a plain hypothermia scene, and
    a lone body word ("toe") opened a fracture card with no injury heard."""
    from vr.live import keep_trees
    pool = _s007_pool()
    pool["wfa-cpr"] = ({"root": "s", "title": "CPR", "nodes": {"s": {"label": "Breathing?"}, "cpr": {"label": "Start CPR"}}},
                       {"life_threat": [{"if": {"breathing": "no"}, "go": "cpr"}],
                        "slots": {"breathing": {"no": ["not breathing"]}, "cause": {"cold": ["freezing"]}}})
    pool["hypothermia"] = ({"root": "s", "title": "Hypothermia", "nodes": {"s": {"label": "Shivering?"}, "y": {"label": "Warm"}}},
                           {"slots": {"shivering": {"yes": ["shivering"]}}})
    names = [c.name for c in keep_trees(["he's freezing and shivering"], pool)]
    assert names[0] == "hypothermia" and "wfa-cpr" not in names
    pool["fractures"] = ({"root": "w", "title": "Fractures", "nodes": {"w": {"label": "Where?"}, "toes": {"label": "Toes"}}},
                         {"slots": {"body_part": {"toes": ["toe"]}, "broken": {"yes": ["broken"]}}})
    assert keep_trees(["toe in"], pool) == []
    assert keep_trees(["my toe is broken"], pool)[0].name == "fractures"


def test_named_tree_own_life_rule_beats_generic_cpr():
    """Trees thread 11-18: "stove fumes, she's passed out and not breathing" opened wfa-cpr/cpr and
    skipped CO's protect-yourself step. A tree that heard its own problem and has its own rule for
    the life value opens that rule; a tree whose rules are only on its own blanks does not."""
    from vr.live import keep_trees
    pool = _s007_pool()
    pool["wfa-cpr"] = ({"root": "s", "title": "CPR", "nodes": {"s": {"label": "Breathing?"}, "cpr": {"label": "Start CPR"}}},
                       {"life_threat": [{"if": {"breathing": "no"}, "go": "cpr"}],
                        "slots": {"breathing": {"no": ["not breathing"]}, "conscious": {"no": ["passed out"]}}})
    pool["wfa-co"] = ({"root": "s", "title": "Carbon monoxide", "nodes": {"s": {"label": "Source?"}, "treat_now": {"label": "Get out"}}},
                      {"life_threat": [{"if": {"conscious": "no"}, "go": "treat_now"}],
                       "slots": {"source": {"stove": ["stove fumes"]}, "conscious": {"no": ["passed out"]}}})
    line = "stove fumes, she's passed out and not breathing"
    cands = keep_trees([line], pool)
    assert cands[0].name == "wfa-co"
    assert first_sentence(line, cands) == ("open", "wfa-co", "treat_now")
    assert keep_trees(["she's passed out and not breathing"], pool)[0].name == "wfa-cpr"


def test_heat_stroke_names_heat_not_stroke():
    from vr.form import named_trees
    from vr.intake import tokenize
    trees = {"heat": ({"root": "s", "title": "Heat illness", "nodes": {"s": {}}}, {"topic_words": ["heat stroke"]}),
             "wfa-stroke": ({"root": "s", "title": "Stroke", "nodes": {"s": {}}}, {"topic_words": ["stroke"]})}
    assert named_trees(tokenize("i think it's heat stroke"), trees) == {"heat"}
    assert named_trees(tokenize("i think she's having a stroke"), trees) == {"wfa-stroke"}


def test_place_only_goes_to_start_but_an_injury_word_keeps_the_tree():
    """df95add PC check: the place-only rule sent "i think i broke my leg" to start; and "it
    stopped, he's breathing fine" kept three trees that each scored 0."""
    from vr.live import keep_trees
    pool = _s007_pool()
    pool["fractures"] = ({"root": "w", "title": "Fractures", "nodes": {"w": {"label": "Where?"}, "leg": {"label": "Leg"}}},
                         {"slots": {"body_part": {"leg": ["leg"], "toes": ["toe"]}, "breathing": {"yes": ["breathing fine"]}}})
    assert keep_trees(["my toe"], pool) == []
    assert keep_trees(["his leg"], pool) == []
    assert keep_trees(["i think i broke my leg"], pool)[0].name == "fractures"
    assert keep_trees(["she rolled her leg on a rock"], pool)[0].name == "fractures"
    assert keep_trees(["it stopped, he's breathing fine"], pool) == []


def test_lower_tree_plain_jump_to_other_tree_does_not_open():
    """4108045: "i think it's heat stroke" kept [heat, fainting]; fainting's cause=heat walked to
    heat/syncope and opened it over heat's own form. Only a life rule may open from a lower tree."""
    heat = Candidate("heat", {"root": "t", "title": "Heat illness", "nodes": {
        "t": {"label": "Which?"}, "ehs": {"label": "Heat stroke"}, "fluid_q": {"label": "Fluids?"},
        "cramps": {"label": "Cramps"}, "a": {"label": "A"}, "b": {"label": "B"}}},
        {"slots": {"heat_type": {"confused": ["heat stroke"], "cramps": ["cramps"]},
                   "fluid": {"yes": ["drank"], "no": ["no water"]}},
         "questions": {"t": {"slot": "heat_type", "choices": {"confused": "fluid_q", "cramps": "cramps"}},
                       "fluid_q": {"slot": "fluid", "choices": {"yes": "a", "no": "b"}}}}, p=0.67)
    faint = Candidate("wfa-fainting", {"root": "f", "title": "Fainting", "nodes": {
        "f": {"label": "Cause?"}, "o": {"label": "Other"}}},
        {"slots": {"cause": {"heat": ["heat"], "other": ["stood up"]}},
         "questions": {"f": {"slot": "cause", "choices": {"heat": "heat/syncope", "other": "o"}}}}, p=0.33)
    kind, name, card = first_sentence("i think it's heat stroke", [heat, faint])
    assert (kind, name) != ("open", "wfa-fainting")
    fields = open_fields([heat, faint])
    assert fields and fields[0].key == "@topic"


def test_trees_agreeing_on_a_card_open_it_below_sure():
    """47c1afd: "anaphylaxis" kept [allergy 0.67, asthma ...]; allergy's walk landed on severe and
    asthma's jumped there too, but it showed a which-is-it form."""
    allergy = Candidate("wfa-allergy", {"root": "a", "title": "Allergy", "nodes": {
        "a": {"label": "How bad?"}, "severe": {"label": "Severe"}, "mild": {"label": "Mild"}}},
        {"slots": {"severity": {"severe": ["anaphylaxis"], "mild": ["itchy"]}},
         "questions": {"a": {"slot": "severity", "choices": {"severe": "severe", "mild": "mild"}}}}, p=0.67)
    asthma = Candidate("wfa-asthma", {"root": "s", "title": "Asthma", "nodes": {
        "s": {"label": "How bad?"}, "m": {"label": "Mild"}}},
        {"slots": {"severity": {"severe": ["anaphylaxis"], "mild": ["wheezy"]}},
         "questions": {"s": {"slot": "severity", "choices": {"severe": "wfa-allergy/severe", "mild": "m"}}}}, p=0.33)
    assert first_sentence("anaphylaxis", [allergy, asthma]) == ("open", "wfa-allergy", "severe")


def test_one_landing_tree_with_others_asking_does_not_open():
    """04df032 PC check: a low-ranked tree landing on a content card while the others were still
    asking opened it ("too cold and confused" -> HACE). Every kept tree must land there."""
    alt = Candidate("wfa-altitude", {"root": "a", "title": "Altitude", "nodes": {
        "a": {"label": "Which?"}, "hace": {"label": "HACE"}, "ams": {"label": "AMS"}}},
        {"slots": {"alt_type": {"hace": ["confused"], "ams": ["headache"]}},
         "questions": {"a": {"slot": "alt_type", "choices": {"hace": "hace", "ams": "ams"}}}}, p=0.33)
    hypo = Candidate("hypothermia", {"root": "h", "title": "Hypothermia", "nodes": {
        "h": {"label": "Shivering?"}, "mild": {"label": "Mild"}, "severe": {"label": "Severe"}}},
        {"slots": {"shiver": {"yes": ["shivering"], "no": ["stopped shivering"]}},
         "questions": {"h": {"slot": "shiver", "choices": {"yes": "mild", "no": "severe"}}}}, p=0.67)
    assert first_sentence("he's too cold and confused", [hypo, alt])[0] == "form"


def test_shared_cpr_rule_is_not_a_trees_own_handling():
    """Trees 11-18: bites/burns carry the shared {breathing: no} -> wfa-cpr/cpr rule; with a bleeding
    word they outranked wfa-bleeding and CPR on "he's bleeding a lot and not breathing"."""
    from vr.live import keep_trees
    cpr = ({"root": "s", "title": "CPR", "nodes": {"s": {}, "cpr": {}}},
           {"life_threat": [{"if": {"breathing": "no"}, "go": "cpr"}], "slots": {"breathing": {"no": ["not breathing"]}}})
    bites = ({"root": "s", "title": "Bites", "nodes": {"s": {}}},
             {"life_threat": [{"if": {"breathing": "no"}, "go": "wfa-cpr/cpr"}],
              "slots": {"bleeding": {"yes": ["bleeding"]}, "breathing": {"no": ["not breathing"]}}})
    bleed = ({"root": "s", "title": "Bleeding", "nodes": {"s": {}, "heavy": {}}},
             {"life_threat": [{"if": {"bleeding_type": "heavy"}, "go": "heavy"}],
              "slots": {"bleeding_type": {"heavy": ["bleeding a lot"]}}})
    names = [c.name for c in keep_trees(["he's bleeding a lot and not breathing"],
                                        {"bites": bites, "wfa-bleeding": bleed, "wfa-cpr": cpr})]
    assert names.index("bites") > min(names.index("wfa-cpr"), names.index("wfa-bleeding"))


def test_shared_blanks_are_not_a_trees_own_problem():
    """31298ea PC check: wfa-co / wfa-drowning / wfa-diabetic fill the shared cause, bleeding_type
    and breathing blanks; their own not-breathing rule then beat CPR with no CO, water or sugar heard."""
    from vr.live import keep_trees
    shared = {"breathing": {"no": ["not breathing"]}, "bleeding_type": {"heavy": ["bleeding a lot"]},
              "cause": {"hypothermia": ["freezing cold"]}}
    def tree(rule_go, own):
        return ({"root": "s", "title": "T", "nodes": {"s": {}, "x": {}}},
                {"life_threat": [{"if": {"breathing": "no"}, "go": rule_go}], "slots": {**shared, **own}})
    pool = {"wfa-cpr": ({"root": "s", "title": "CPR", "nodes": {"s": {}, "cpr": {}}},
                        {"life_threat": [{"if": {"breathing": "no"}, "go": "cpr"}], "slots": dict(shared)}),
            "wfa-co": tree("x", {"co_source": {"stove": ["stove fumes"]}}),
            "wfa-drowning": tree("x", {"in_water": {"yes": ["pulled him out of the water"]}}),
            "wfa-bleeding": ({"root": "s", "title": "Bleeding", "nodes": {"s": {}}}, {"slots": dict(shared)})}
    for line in ["he's bleeding a lot and not breathing", "he is freezing cold and not breathing"]:
        cands = keep_trees([line], pool)
        assert cands[0].name == "wfa-cpr", (line, [c.name for c in cands])
    assert keep_trees(["stove fumes and she's not breathing"], pool)[0].name == "wfa-co"


def test_life_phrase_listed_in_a_trees_own_slot_is_not_its_own_problem():
    """b843474 PC check s021: diabetic alert=no lists "won't respond"; "ice cold, won't respond"
    gave the diabetic card instead of CPR."""
    from vr.live import keep_trees
    cpr = ({"root": "s", "title": "CPR", "nodes": {"s": {}, "start": {}}},
           {"life_threat": [{"if": {"conscious": "no"}, "go": "start"}],
            "slots": {"conscious": {"no": ["won't respond"]}, "cause": {"cold": ["ice cold"]}}})
    diab = ({"root": "s", "title": "Diabetic", "nodes": {"s": {}, "drowsy": {}}},
            {"life_threat": [{"if": {"conscious": "no"}, "go": "drowsy"}],
             "slots": {"conscious": {"no": ["won't respond"]}, "alert": {"no": ["won't respond", "drowsy"]},
                       "cause": {"cold": ["ice cold"]}}})
    pool = {"wfa-cpr": cpr, "wfa-diabetic": diab}
    assert keep_trees(["he is ice cold and won't respond"], pool)[0].name == "wfa-cpr"
    assert keep_trees(["she's diabetic and drowsy and won't respond"], pool)[0].name == "wfa-diabetic"


def _tabtree(title, label):
    return {"root": "start", "title": title, "nodes": {"start": {"label": label}, "severe": {"label": "Severe"},
            "arm": {"label": "Arm"}}}


def test_several_problems_put_severe_bleeding_tab_first_on_its_card():
    from vr.form import split_tabs
    frac = Candidate("fractures", _tabtree("Fractures", "Where?"),
                     {"slots": {"broken": {"leg": ["leg is broken"]}}, "questions": {}}, p=0.6)
    bleed = Candidate("wfa-bleeding", _tabtree("Bleeding", "How bad?"),
                      {"life_threat": [{"if": {"amount": "heavy"}, "go": "severe"}],
                       "slots": {"amount": {"heavy": ["bleeding a lot"]}}}, p=0.3)
    cands = [frac, bleed]
    assert first_sentence("his arm is bleeding a lot and his leg is broken", cands, multi=True)[0] == "tabs"
    tabs = split_tabs(cands)
    assert [t[0].name for t in tabs] == ["wfa-bleeding", "fractures"] and tabs[0][0].card == "severe"


def test_life_tab_order_bleeding_cpr_choking():
    from vr.form import life_rank
    mk = lambda n: Candidate(n, _tabtree(n, "x"), {"life_threat": [{"if": {"a": "y"}, "go": "severe"}], "slots": {}})
    cs = [mk(n) for n in ("wfa-choking", "wfa-cpr", "wfa-bleeding", "wfa-allergy", "heat")]
    for c in cs:
        c.filled = {"a": "y"}
    assert sorted(cs, key=life_rank)[0].name == "wfa-bleeding"
    assert [c.name for c in sorted(cs, key=life_rank)] == ["wfa-bleeding", "wfa-cpr", "wfa-choking", "wfa-allergy", "heat"]


def test_one_tab_per_life_card_and_the_jumper_keeps_its_own_problem():
    from vr.form import split_tabs
    rule = [{"if": {"bleeding_type": "heavy"}, "go": "wfa-bleeding/severe"}]
    bt = {"heavy": ["bleeding a lot"]}
    frac = Candidate("fractures", {"root": "start", "title": "Fractures", "nodes": {"start": {"label": "Where?"},
                     "leg": {"label": "Broken leg"}}},
                     {"life_threat": rule, "slots": {"bleeding_type": bt, "where": {"leg": ["leg is broken"]}},
                      "questions": {"start": {"slot": "where", "choices": {"leg": "leg"}}}}, p=0.5)
    gear = Candidate("improvised_gear", {"root": "start", "title": "Gear", "nodes": {"start": {"label": "What's missing?"}}},
                     {"life_threat": rule, "slots": {"bleeding_type": bt}}, p=0.3)
    bleed = Candidate("wfa-bleeding", _tabtree("Bleeding", "How bad?"),
                      {"life_threat": [{"if": {"bleeding_type": "heavy"}, "go": "severe"}], "slots": {"bleeding_type": bt}}, p=0.2)
    cands = [frac, gear, bleed]
    assert first_sentence("his arm is bleeding a lot and his leg is broken", cands, multi=True)[0] == "tabs"
    tabs = split_tabs(cands)
    assert [(t[0].name, t[0].card) for t in tabs] == [("wfa-bleeding", "severe"), ("fractures", "leg")]
    assert [(t[0].name, t[0].card) for t in split_tabs(cands)] == [("wfa-bleeding", "severe"), ("fractures", "leg")]


def test_two_trees_landing_on_one_plain_card_share_a_tab():
    from vr.form import split_tabs
    heat = Candidate("heat", {"root": "start", "title": "Heat", "nodes": {"start": {"label": "Heat?"}, "syncope": {"label": "Fainted"}}},
                     {"slots": {"heat": {"yes": ["hot day"]}}, "questions": {"start": {"slot": "heat", "choices": {"yes": "syncope"}}}}, p=0.4)
    faint = Candidate("wfa-fainting", {"root": "start", "title": "Fainting", "nodes": {"start": {"label": "Cause?"}}},
                      {"slots": {"cause": {"heat": ["fainted"]}}, "questions": {"start": {"slot": "cause", "choices": {"heat": "heat/syncope"}}}}, p=0.5)
    burn = Candidate("burns", BURNS, BSMAP, p=0.3)
    cands = [faint, heat, burn]
    first_sentence("she fainted on a hot day and has a big burn", cands, multi=True)
    cards = [t[0].card if t[0].card not in t[0].tree["nodes"] else f"{t[0].name}/{t[0].card}" for t in split_tabs(cands)]
    assert len(cards) == len(set(cards))


def test_place_only_match_loses_its_tile_to_trees_with_own_evidence():
    """Board 11-22: 'eye think my friend femur is broken ... can't walk' kept burns on body_part=eye."""
    from vr.live import keep_trees
    def t(slots, q):
        return ({"root": "s", "nodes": {"s": {"label": "?"}, "a": {}}},
                {"slots": slots, "questions": {"s": {"slot": q, "choices": {next(iter(slots[q])): "a"}}}})
    pool = {"burns": t({"body_part": {"eye": ["eye"]}}, "body_part"),
            "fractures": t({"body_part": {"femur": ["femur"]}, "injury": {"broken": ["is broken"]}}, "injury"),
            "wfa-bleeding": t({"body_part": {"limb": ["leg"]}}, "body_part"),
            "wfa-sprains": t({"can_use": {"no": ["can't walk"]}}, "can_use")}
    kept = [c.name for c in keep_trees(["eye think my friend femur is broken eat camp put any way on legs can't walk"], pool)]
    assert kept[:2] == ["fractures", "wfa-sprains"] and "burns" not in kept
