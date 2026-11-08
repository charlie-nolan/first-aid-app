from plb import verbatim as vb
from plb import voicetest as vt


PAGE = ("23 March 2026 ATP 4-02.11 49\n4-41. One-Handed TQ Application:\n"
        "• Position the TQ above the bleeding site, high on the\nextremity.\n"
        "• Do not loosen the TQ once it is life￾\nthreatening.\n"
        "CAUTION:\nIf an object is lodged, do not remove the object.\n")


def test_exact_ignores_list_markers_and_line_breaks():
    card = "One-Handed TQ Application:\n- Position the TQ above the bleeding site, high on the extremity."
    assert vb.compare_chunk(card, PAGE)[0] == "exact"


def test_card_heading_for_a_box_matches_the_page_label():
    card = "## CAUTION\nIf an object is lodged, do not remove the object."
    assert vb.compare_chunk(card, PAGE)[0] == "exact"


def test_glued_line_end_hyphen_is_layout_with_a_note():
    lvl, diff = vb.compare_chunk("Do not loosen the TQ once it is lifethreatening.", PAGE)
    assert lvl == "layout"
    assert diff and diff[0]["minor"] and diff[0]["card"] == "lifethreatening"


def test_dropped_word_is_a_mismatch():
    lvl, diff = vb.compare_chunk("- Do loosen the TQ once it is life-threatening.", PAGE)
    assert lvl == "MISMATCH"
    assert any(d["pdf"] == "not" and not d["card"] for d in diff)


def test_changed_number_is_a_mismatch():
    page = "Hold pressure for a minimum of 3 minutes."
    assert vb.compare_chunk("Hold pressure for a minimum of 2 minutes.", page)[0] == "MISMATCH"


def test_degree_sign_is_spelled_out_like_the_extractor():
    assert vb.compare_chunk("below 95 degrees F", "below 95°F")[0] == "layout"


def test_similarity_basics():
    assert vt.similarity("tourniquet", "tourniquet") == 1.0
    assert vt.similarity("turniket", "tourniquet") > vt.similarity("blister", "tourniquet")
    # the hazard the stress test exists to catch: one short word flips the meaning
    assert vt.similarity("i have a tourniquet", "i dont have a tourniquet") > vt.THRESHOLD


def _cmd(phrase, key, entry=False):
    return {"phrase": phrase, "key": key, "tree": key.split("/")[0], "entry": entry,
            "always": False, "node": hash(key)}


def test_classify_outcomes():
    up = {"t/b": {"t/a"}, "t/c": {"t/a"}}
    ids = {"t"}
    u = {"text": "x", "want": ["t/b"], "topic": "t", "off_topic": False}
    assert vt.classify(u, [(0.9, _cmd("x", "t/b"))], 0.7, ids, up) == "hit"
    assert vt.classify(u, [(0.9, _cmd("x", "t/a", entry=True))], 0.7, ids, up) == "near"
    assert vt.classify(u, [(0.9, _cmd("x", "t/c"))], 0.7, ids, up) == "OFF"
    assert vt.classify(u, [(0.5, _cmd("x", "t/b"))], 0.7, ids, up) == "miss"
    off = {"text": "bee sting", "want": ["none"], "topic": "allergy", "off_topic": True}
    assert vt.classify(off, [(0.5, _cmd("x", "t/b"))], 0.7, ids, up) == "hit"
    assert vt.classify(off, [(0.8, _cmd("x", "t/b"))], 0.7, ids, up) == "FALSE"


def test_synthetic_file_loads():
    from pathlib import Path
    rows = vt.load_utterances(Path(__file__).parents[1] / "content" / "phrases" / "synthetic")
    assert len(rows) > 200
    assert any(r["off_topic"] for r in rows) and any(not r["off_topic"] for r in rows)
