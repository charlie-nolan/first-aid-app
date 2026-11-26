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


def test_i_eye_homophone():
    """Board test 2026-11-24: 'I fell on my arm' came out as 'eye fell'. Same phones (AY)."""
    from fasr.decode import resolve_homophones
    trie = Trie.from_lexicon(ROWS)
    ids = {r["word"]: r["id"] for r in ROWS}
    hyp = {"h": [{"ids": [ids["eye"], ids["fell"], ids["on"], ids["my"], ids["arm"]]},
                 {"ids": [ids["something"], ids["in"], ids["my"], ids["i"]]}]}
    out = resolve_homophones(hyp, trie)
    assert out["h"][0]["ids"][0] == ids["i"]
    assert out["h"][1]["ids"][3] == ids["eye"]
    cases = [(["scratch", "eye", "ball"], 1, "eye"),   # next word only follows "eye"
             (["i", "poked", "i"], 2, "eye"),          # verb done to an eye
             (["i", "poked", "i"], 0, "i"),
             (["eye", "hurt", "it"], 0, "i")]
    for words, pos, want in cases:
        got = resolve_homophones({"h": [{"ids": [ids[w] for w in words]}]}, trie)["h"][0]["ids"]
        assert trie.name[got[pos]] == want, (words, pos)


# ------------------------------------------------------------------ Stage 3 tails
def _tiny_q(seed=0):
    import torch
    from fasr.export_int8 import fold, quantise
    from fasr.model import AcousticModel
    torch.manual_seed(seed)
    m = AcousticModel("xs").eval()
    for mod in m.modules():
        if isinstance(mod, torch.nn.BatchNorm1d):
            mod.running_mean.uniform_(-0.2, 0.2); mod.running_var.uniform_(0.5, 1.5)
    rng = np.random.default_rng(seed)
    feats = [rng.normal(size=(120, 40)).astype(np.float32) for _ in range(3)]
    return m, fold(m), feats, quantise(fold(m), feats)


def test_tail_from_h_is_bit_identical():
    from fasr.export_int8 import forward_q, quantise, run_q
    from fasr.tail import SPLIT
    m, layers, feats, q = _tiny_q()
    k = SPLIT["xs"]; n_trunk = 1 + 2 * k
    x_q, outs, acc = forward_q(q, feats[0])
    h = outs[f"b{k - 1}.pw"]
    touts, tacc = run_q(q["layers"][n_trunk:], h)
    assert np.array_equal(tacc, acc)
    assert q["layers"][n_trunk]["e_in"] == q["layers"][n_trunk - 1]["e_out"] == q["layers"][n_trunk + 1]["e_res"]
    # fixed exponents (the device base model's) win over calibration
    fixed = {"input": q["e_in"] - 1, f"b{k - 1}.pw": q["layers"][n_trunk - 1]["e_out"] - 1}
    q2 = quantise(layers, feats, fixed=fixed)
    assert q2["e_in"] == q["e_in"] - 1 and q2["layers"][n_trunk]["e_in"] == q["layers"][n_trunk - 1]["e_out"] - 1


def test_tail_pack_matches_asrpack(tmp_path):
    import importlib.util
    import json
    from fasr.export_int8 import write_bin
    from fasr.tail import EXPT_HDR, SPLIT, pack_expt, pack_fq81
    _, _, _, q = _tiny_q(1)
    n_trunk = 1 + 2 * SPLIT["xs"]
    tq = {"e_in": q["layers"][n_trunk - 1]["e_out"], "layers": q["layers"][n_trunk:], "mu": q["mu"], "sd": q["sd"]}
    meta = write_bin(tq, tmp_path)
    meta.update(format="FASR-Q8 v1", e_in=tq["e_in"], mu=q["mu"].tolist(), sd=q["sd"].tolist())
    (tmp_path / "model.json").write_text(json.dumps(meta))
    spec = importlib.util.spec_from_file_location(
        "asrpack", Path(__file__).resolve().parents[3] / "firmware" / "components" / "asr" / "tools" / "asrpack.py")
    ap = importlib.util.module_from_spec(spec); spec.loader.exec_module(ap)
    ap.main([str(tmp_path), "-o", str(tmp_path / "asrm.bin")])
    fq = pack_fq81(meta, (tmp_path / "model.bin").read_bytes())
    assert fq == (tmp_path / "asrm.bin").read_bytes()
    e = pack_expt(3, "burns", b"12345678", n_trunk, len(q["layers"]), tq["e_in"], fq)
    assert e[:4] == b"EXPT" and len(e) == EXPT_HDR + len(fq) and e[EXPT_HDR:EXPT_HDR + 4] == b"FQ81"
    import struct
    ver, gid = struct.unpack_from("<HH", e, 4)
    a_, b_, eh, n = struct.unpack_from("<HHb3xI", e, 16)
    assert (ver, gid, e[8:16], a_, b_, eh, n) == (1, 3, b"12345678", n_trunk, len(q["layers"]), tq["e_in"], len(fq))
    assert e[28:44].rstrip(b"\0") == b"burns"


def test_freeze_trunk_keeps_trunk_and_bn_stats():
    import torch
    from fasr.model import AcousticModel
    from fasr.train import freeze_trunk
    torch.manual_seed(0)
    m = AcousticModel("xs")
    frozen = freeze_trunk(m, 6)
    before = {k: v.clone() for k, v in m.state_dict().items()}
    opt = torch.optim.AdamW([p for p in m.parameters() if p.requires_grad], lr=1e-2)
    m.train(); [f.eval() for f in frozen]
    for _ in range(3):
        loss = m(torch.randn(2, 80, 40)).mean()
        opt.zero_grad(); loss.backward(); opt.step()
    after = m.state_dict()
    for k in before:
        trunk = k.split(".")[0] in ("stem", "bn0", "mu", "sd") or (k.startswith("blocks.") and int(k.split(".")[1]) < 6)
        if trunk:
            assert torch.equal(before[k], after[k]), k
    assert not torch.equal(before["head.weight"], after["head.weight"])
    assert not torch.equal(before["blocks.7.pw.weight"], after["blocks.7.pw.weight"])


def test_tail_groups():
    from fasr.tail import LIFE_CORE, clip_groups, in_group, load_groups
    g = load_groups()
    assert list(g)[:5] == ["bleeding", "bones", "breathing", "burns", "environment"]
    assert clip_groups("my leg is bleeding", g)[0] == {"bleeding"}
    assert clip_groups("she twisted her ankle", g)[0] == {"bones"}
    assert clip_groups("he can't move his arm", g)[0] == set()        # body parts alone pick no group
    r = {"text": "he's not breathing"}
    assert all(in_group(r, k, g) for k in g)                           # life clips train every tail
    ids = {x["word"] for x in ROWS}
    assert all(w in ids for w in LIFE_CORE)
