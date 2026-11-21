import sys
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "voice_router"))

from fasr import lexicon as lx  # noqa: E402
from fasr.decode import Trie, nbest  # noqa: E402
from fasr.features import logmel, logmel_torch  # noqa: E402
from fasr.phones import BLANK, N_TOKENS, SEP, TOK  # noqa: E402

ROWS = yaml.safe_load(lx.LEXICON.read_text())["words"]


def test_lexicon_contract():
    assert [r["word"] for r in ROWS[:10]] == lx.RESERVED
    assert [r["id"] for r in ROWS] == list(range(len(ROWS)))
    assert len(ROWS) <= lx.CAP
    byw = {r["word"]: r for r in ROWS}
    assert byw["can't"]["class"] == 4 and byw["no"]["class"] == 4 and byw["ankle"]["class"] == 1
    for r in ROWS[1:]:
        assert r["phones"], r["word"]
        for p in r["phones"]:
            assert all(x in TOK for x in p.split()), (r["word"], p)


def test_tokenizer_matches_router():
    from vr.lexicon import tokenize
    for s in ["He's bleeding, can't move!", "my 2 toes’re numb", "i don't know"]:
        assert lx.tokenize(s) == tokenize(s)


def test_append_only():
    rows, _ = lx.build([lx.CONTENT / "trees"], [], existing=ROWS)
    assert [(r["id"], r["word"]) for r in rows[: len(ROWS)]] == [(r["id"], r["word"]) for r in ROWS]


def _fake(words):
    byw = {r["word"]: r for r in ROWS}
    toks = []
    for i, w in enumerate(words):
        if i:
            toks.append(SEP)
        toks += [TOK[x] for x in byw[w]["phones"][0].split()]
    fr = [f for t in toks for f in (t, t, BLANK)]
    lp = np.full((len(fr), N_TOKENS), -8.0)
    lp[np.arange(len(fr)), fr] = -0.05
    return lp - np.log(np.exp(lp).sum(1, keepdims=True))


def test_decode_words():
    trie = Trie.from_lexicon(ROWS)
    ids = {r["word"]: r["id"] for r in ROWS}
    r = nbest(_fake(["my", "ankle", "is", "swollen"]), trie)
    assert r["h"][0]["ids"] == [ids[w] for w in ["my", "ankle", "is", "swollen"]]
    assert r["reject"] < 10 and r["n"] <= 5


def test_features_match():
    x = np.random.default_rng(0).standard_normal(16000).astype(np.float32) * 0.1
    import torch
    a = logmel(x)
    b = logmel_torch(torch.from_numpy(x)[None])[0].numpy()
    assert a.shape == b.shape and np.allclose(a, b, atol=1e-3)


def test_long_sentence_still_decodes():
    """Regression (tether test 2026-11-17): >16 words gave n=0 / reject 100. Now fillers are
    dropped to fit voice_hyp_t and the keywords survive."""
    trie = Trie.from_lexicon(ROWS)
    ids = {r["word"]: r["id"] for r in ROWS}
    s = [w for w in ("my buddy slipped on the rocks and his ankle is all swollen and he can not put "
                     "weight on it and it is bleeding a lot").split() if w in ids]
    assert len(s) > 16
    r = nbest(_fake(s), trie)
    assert r["n"] > 0 and r["reject"] < 50
    top = r["h"][0]["ids"]
    assert len(top) <= 16
    for w in ("ankle", "swollen", "bleeding", "not"):
        assert ids[w] in top


def test_device_frontend_close_to_numpy():
    """E1: the device's float32 C front end stays within float32 rounding of the numpy reference."""
    from fasr.features import logmel_np
    x = (np.random.default_rng(1).standard_normal(16000) * 3000).astype(np.int16)
    a, b = logmel(x), logmel_np(x)
    assert a.shape == b.shape and np.abs(a - b).max() < 1e-3
