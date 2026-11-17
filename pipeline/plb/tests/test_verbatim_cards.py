"""plb verbatim --cards: which differences count as reading order (ORDER) and which fail."""
from plb.verbatim import _only_reordered


def test_reordered_columns_are_order():
    page = "Sweating. Two-column text here. Nausea and vomiting. Headache."
    assert _only_reordered("Headache. Sweating. Nausea and vomiting.", page)


def test_card_word_not_on_page_fails():
    page = "Apply direct pressure to the wound."
    assert not _only_reordered("Do not apply direct pressure to the wound.", page)


def test_short_word_never_excused_by_a_longer_page_word():
    # "not" must be printed as a word; "note" on the page does not excuse it
    assert not _only_reordered("Do not move them.", "Note: Do move them.")


def test_pdf_text_layer_artefacts():
    assert _only_reordered("Repeat the injection if the first one fails.",
                           "Repeat the injection if the fi rst one fails.")          # split ligature
    assert _only_reordered("apply a stretch bandage (ACE wrap)", "apply a stretch bandage (ACETM wrap)")
    assert _only_reordered("a bath between 100-102 degrees F", "a bath between 100-1020 F")


def test_list_numbers_excused_but_doses_kept():
    assert _only_reordered("confusion, 7. Nausea", "confusion Nausea")
    assert _only_reordered("0.3 to 0.5 mg of Epinephrine", "OR administration of 0.3 to \n0.5 mg of Epinephrine")
    assert not _only_reordered("0.3 to 0.5 mg of Epinephrine", "0.3 to 0.4 mg of Epinephrine")
