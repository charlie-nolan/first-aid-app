"""Output tokens of the acoustic model: CTC blank, word boundary, 39 ARPAbet phones.

The model only ever learns these 41 sounds. Words live in lexicon.yaml as phone strings,
so adding a word never retrains the network.
"""
from __future__ import annotations

PHONES = ["AA", "AE", "AH", "AO", "AW", "AY", "B", "CH", "D", "DH", "EH", "ER", "EY", "F", "G",
          "HH", "IH", "IY", "JH", "K", "L", "M", "N", "NG", "OW", "OY", "P", "R", "S", "SH", "T",
          "TH", "UH", "UW", "V", "W", "Y", "Z", "ZH"]
BLANK, SEP = 0, 1
TOKENS = ["<b>", "|"] + PHONES
TOK = {t: i for i, t in enumerate(TOKENS)}
N_TOKENS = len(TOKENS)


def encode_words(prons: list[list[str]]) -> list[int]:
    """[[phones of word 1], [phones of word 2], ...] -> token ids with | between words."""
    out: list[int] = []
    for i, p in enumerate(prons):
        if i:
            out.append(SEP)
        out += [TOK[x] for x in p]
    return out
