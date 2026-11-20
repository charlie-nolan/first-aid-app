from vr.search import PAGE, build_index, frame, pages, search

BITES = ({"root": "start", "title": "Bites", "phrases": ["something bit me"], "nodes": {
    "start": {"label": "What bit you?", "choices": [{"label": "Snake", "to": "snake"}, {"label": "Dog or other mammal", "to": "mammal"}]},
    "snake": {"label": "Snake bite", "phrases": ["rattlesnake bite"]},
    "mammal": {"label": "Where is the bite?", "choices": [{"label": "Arm or leg", "to": "limb"}]},
    "limb": {"label": "Bite on an arm or leg"}}},
    {"life_threat": {"bleeding": {"severe": "wfa-bleeding/severe"}},
     "slots": {"animal": {"snake": ["rattler"], "mammal": ["dog", "raccoon"]},
               "bleeding": {"severe": ["spurting", "gushing"]}},
     "questions": {"start": {"slot": "animal", "choices": {"snake": "snake", "mammal": "mammal"}}}})
BLEED = ({"root": "start", "title": "Bleeding", "nodes": {
    "start": {"label": "How bad is the bleeding?", "choices": [{"label": "Spurting", "to": "severe"}]},
    "severe": {"label": "Severe bleeding: press hard"}, "minor": {"label": "Small cut"}}},
    {"slots": {"amount": {"heavy": ["a lot"]}}})
TREES = {"bites": BITES, "wfa-bleeding": BLEED}


def nodes(q):
    return [h["node"] for h in search(q, TREES)]


def test_topic_word_lists_the_root_first_then_the_whole_topic():
    assert nodes("bites")[0] == "bites/start"
    assert set(nodes("bites")) == {"bites/start", "bites/snake", "bites/mammal", "bites/limb"}


def test_life_cards_come_first_even_with_a_lower_score():
    out = nodes("bleeding")
    assert out[0] == "wfa-bleeding/severe" and out[1] == "wfa-bleeding/start"


def test_slot_phrases_reach_the_card_their_answer_leads_to():
    assert nodes("rattler") == ["bites/snake"]
    assert nodes("raccoon") == ["bites/mammal"]


def test_life_rule_words_reach_a_card_in_another_tree_listed_once():
    out = search("spurting", TREES)
    assert [h["node"] for h in out] == ["wfa-bleeding/severe"] and out[0]["life"]


def test_every_word_of_a_phrase_must_match():
    assert nodes("snake bite") == ["bites/snake"]
    assert nodes("snake leg") == []


def test_filler_and_unknown_words_find_nothing():
    assert nodes("the") == [] and nodes("zebra") == []


def test_word_forms_match():
    assert "bites/snake" in nodes("rattlesnakes")


def test_frame_pages_of_eight():
    big = {f"t{i}": ({"root": "start", "title": f"Burn {i}", "nodes": {"start": {"label": "Burn"}}}, {}) for i in range(19)}
    f = frame("burn", big, build_index(big))
    assert f["kind"] == "search" and f["hits"] == 19 and [len(p) for p in f["pages"]] == [PAGE, PAGE, 3]
    assert pages([1, 2, 3], 2) == [[1, 2], [3]]


def test_weak_life_match_does_not_bury_the_topic():
    eye = ({"root": "start", "title": "Eye injury", "nodes": {"start": {"label": "Something in the eye"},
            "wash": {"label": "Wash the eye"}}}, {})
    cpr = ({"root": "start", "title": "CPR", "nodes": {"start": {"label": "Breathing?"}, "cpr": {"label": "Start CPR"}}},
           {"life_threat": [{"if": {"conscious": "no"}, "go": "cpr"}],
            "slots": {"conscious": {"no": ["won't open her eyes", "eyes rolled back"]}}})
    t = {"wfa-eye": eye, "wfa-cpr": cpr}
    out = [h["node"] for h in search("eyes", t)]
    assert out[0] == "wfa-eye/start" and "wfa-cpr/cpr" in out


def test_shared_blank_phrases_are_not_indexed_outside_home():
    a = ({"root": "start", "title": "Alpha", "nodes": {"start": {"label": "A"}, "x": {"label": "X"}}},
         {"life_threat": [{"if": {"bleeding_type": "heavy"}, "go": "x"}], "slots": {"bleeding_type": {"heavy": ["gushing"]}}})
    t = {"a": a, "b": ({**a[0], "title": "Beta"}, a[1]), "c": ({**a[0], "title": "Gamma"}, a[1])}
    assert search("gushing", t) == []


def test_blood_also_finds_bleeding():
    assert "wfa-bleeding/start" in nodes("blood")


def test_naming_the_topic_keeps_its_life_card_first():
    t = {"wfa-nosebleed": ({"root": "start", "title": "Nosebleed", "nodes": {
            "start": {"label": "Nosebleed", "phrases": ["nosebleed", "bloody nose"]}, "treat": {"label": "Pinch the nose"},
            "persist": {"label": "Won't stop after 30 minutes"}}},
         {"life_threat": [{"if": {"amount": "lots"}, "go": "persist"}], "slots": {"amount": {"lots": ["won't stop"]}}})}
    assert [h["node"] for h in search("nosebleed", t)][0] == "wfa-nosebleed/persist"
