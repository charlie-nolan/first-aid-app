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
