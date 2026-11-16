"""Train, quantize, evaluate and export the router and experts.

  python -m vr router  --trees DIR [--out OUT] [--d 64] [--hidden 128] [--buckets 4096]
  python -m vr expert TREE --trees DIR [--out OUT]
  python -m vr sweep   --trees DIR            # size vs accuracy table

Reports compare against a "fixed phrase" baseline: the train sentence that shares the
most words with what was heard (roughly what MultiNet does when an exact phrase is
missing: it snaps to the nearest one in its list).
"""
from __future__ import annotations

import argparse
import json
import random
import time
from collections import Counter
from pathlib import Path

import numpy as np
import torch

import yaml

from .data import Example, ExpertData, RouterData
from .lexicon import Lexicon
from .model import Net, Packed, features, softmax
from .noise import Confusions, nbest

HERE = Path(__file__).resolve().parents[1]
CONTENT = HERE.parent / "plb" / "content"
LEVELS = [0.0, 0.15, 0.3]
ACCEPT_P, ACCEPT_MARGIN = 0.55, 0.2     # below this the device shows a pick-one-of-3 screen
SAY_AGAIN_P = 0.08     # below this, "say that again"; otherwise never a dead end: show the tiles


def build_lexicon(texts):
    p = CONTENT / "lexicon.yaml"
    return Lexicon.load_yaml(p) if p.exists() else Lexicon.harvest(texts)


def make_batch(exs, lex, conf, rng, buckets, noisy=3, ctx_fn=None):
    out = []
    for e in exs:
        ids = lex.encode(e.text)
        reps = [0.0] if e.weight < 1 else [0.0] + [rng.uniform(0.05, 0.4) for _ in range(noisy)]
        for lv in reps:
            nb = nbest(ids, conf, lv, rng, n=rng.randint(1, 4) if lv else 1)
            wi, ww, hi, hw = features(nb, lex, buckets)
            if rng.random() < 0.15 and len(wi) > 2:          # word dropout
                k = rng.randrange(len(wi)); wi = wi[:k] + wi[k + 1:]; ww = ww[:k] + ww[k + 1:]
            c = ctx_fn(e.label, rng) if ctx_fn else -1
            out.append(((wi, ww, hi, hw, c), e.label, e.weight))
    return out


def train(net, data, lex, conf, epochs, seed, ctx_fn=None, log=None):
    torch.manual_seed(seed)
    rng = random.Random(seed)
    opt = torch.optim.AdamW(net.parameters(), lr=3e-3, weight_decay=1e-4)
    pool = data.train + data.labels
    for ep in range(epochs):
        net.train()
        b = make_batch(pool, lex, conf, rng, net.buckets, ctx_fn=ctx_fn)
        rng.shuffle(b)
        tot = 0.0
        for i in range(0, len(b), 64):
            chunk = b[i:i + 64]
            logits = net([x for x, _, _ in chunk])
            y = torch.tensor([l for _, l, _ in chunk])
            w = torch.tensor([wt for _, _, wt in chunk])
            loss = (torch.nn.functional.cross_entropy(logits, y, reduction="none", label_smoothing=0.05) * w).mean()
            opt.zero_grad(); loss.backward(); opt.step()
            tot += loss.item() * len(chunk)
        if log and (ep % 10 == 0 or ep == epochs - 1):
            log(f"  epoch {ep:3d} loss {tot / len(b):.3f}")
    net.eval()
    return net


def nn_baseline(train, lex):
    """Nearest train sentence by word overlap (Jaccard) -> its label."""
    bank = [(set(lex.encode(e.text)) - {0}, e.label) for e in train if e.weight >= 0.5]

    def pick(heard: set):
        best = max(bank, key=lambda b: len(b[0] & heard) / (len(b[0] | heard) or 1))
        return best[1]
    return pick


def evaluate(packed, data, lex, conf, ctx_mode=None, same=None, seed=7, reps=3, test=None):
    rng = random.Random(seed)
    test = data.test if test is None else test
    base = nn_baseline(data.train, lex)
    same = same or {}
    res = {}
    margin_of = lambda p, o: p[o[0]] - p[o[1]]
    for lv in LEVELS:
        rows_seen = []
        c = Counter()
        n = 0
        for e in test:
            for _ in range(reps if lv else 1):
                nb = nbest(lex.encode(e.text), conf, lv, rng, n=3 if lv else 1)
                ok = same.get(e.label, {e.label})
                ctx = ctx_mode(e.label, rng) if ctx_mode else -1
                p = softmax(packed.logits(nb, ctx))
                order = list(np.argsort(-p))
                top1, top3 = order[0], order[:3]
                c["top1"] += top1 in ok
                c["top3"] += bool(ok & set(top3))
                c["top6"] += bool(ok & set(order[:6]))           # "None of these" -> next 3
                c["rank_sum"] += next(i for i, o in enumerate(order) if o in ok)
                rows_seen.append((float(p[top1]), float(margin_of(p, order)), top1 in ok))
                margin = p[order[0]] - p[order[1]]
                if p[top1] >= ACCEPT_P and margin >= ACCEPT_MARGIN:
                    c["auto_right" if top1 in ok else "auto_wrong"] += 1
                elif p[top1] < SAY_AGAIN_P:
                    c["say_again"] += 1
                else:
                    c["picker_right" if ok & set(top3) else "picker_miss"] += 1
                heard = {i for h in nb for i, _ in h} - {0}
                c["baseline"] += base(heard) in ok
                n += 1
        res[lv] = {k: v / n for k, v in c.items()} | {"n": n, "safe": safe_threshold(rows_seen)}
    return res


def safe_threshold(rows):
    """Lowest accept probability at which nothing in this set opens the wrong card on its own,
    and how many would still open straight away. (Tune on a dev split once there is one.)"""
    wrong = [p for p, m, ok in rows if not ok and m >= ACCEPT_MARGIN]
    t = max(wrong, default=0.0) + 1e-3
    t = max(t, ACCEPT_P)
    auto = sum(1 for p, m, ok in rows if p >= t and m >= ACCEPT_MARGIN)
    return {"p": round(t, 3), "auto": auto / max(1, len(rows))}


def size_of(packed):
    return len(packed.to_bytes())


def report(title, res, packed, extra=""):
    lines = [f"## {title}", "", f"Model: {size_of(packed) / 1024:.0f} KB on flash "
             f"(d={packed.d}, hidden={packed.hidden}, {len(packed.W)} words, {len(packed.Hh)} hash rows, "
             f"{packed.k} classes). {extra}", "",
             "| recognizer noise | top-1 | top-3 | top-6 (after \"none of these\") | auto, right | auto, WRONG | pick-of-3 has it | say again | zero-wrong threshold (opens on its own) | fixed-phrase baseline top-1 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for lv, r in res.items():
        g = lambda k: f"{100 * r.get(k, 0):.0f}%"
        sf = r["safe"]
        lines.append(f"| {lv:.2f} | {g('top1')} | {g('top3')} | {g('top6')} | {g('auto_right')} | {g('auto_wrong')} | "
                     f"{g('picker_right')} | {g('say_again')} | p>={sf['p']:.2f} ({100 * sf['auto']:.0f}%) | {g('baseline')} |")
    return "\n".join(lines) + "\n"


def add_extra(a, data, kind, tree=None):
    """Generated sentences (vr.generate) join training at weight 0.7: helpful, less trusted."""
    ex = getattr(a, "extra", None)
    if not ex:
        return
    if kind == "router" and (ex / "router.yaml").exists():
        g = yaml.safe_load((ex / "router.yaml").read_text())["topics"]
        for k, n in enumerate(data.names[:-1]):
            data.train += [Example(u, k, 0.7) for u in g.get(n, [])]
    if kind == "expert" and (ex / f"expert-{tree}.yaml").exists():
        g = yaml.safe_load((ex / f"expert-{tree}.yaml").read_text())["nodes"]
        idx = {k: i for i, k in enumerate(data.nodes)}
        for k, us in g.items():
            data.train += [Example(u, idx[k], 0.7) for u in us]


def teacher_vectors(a, lex, d):
    """Desktop: word vectors from a sentence-embedding model, squeezed to d numbers (PCA).
    Words the seed data never used (gash, sliced) start near words that mean the same."""
    name = getattr(a, "teacher", None)
    if not name:
        return None
    from sentence_transformers import SentenceTransformer
    m = SentenceTransformer(name)
    e = m.encode(lex.words, normalize_embeddings=True, batch_size=256)
    e = e - e.mean(0)
    u, sv, vt = np.linalg.svd(e, full_matrices=False)
    z = e @ vt[:d].T
    return (z / (z.std() + 1e-6) * 0.1).astype(np.float32)


def run_router(a, log=print):
    data = RouterData(CONTENT / "voice" / "topics.yaml", a.trees)
    add_extra(a, data, "router")
    frac = getattr(a, "frac", 1.0)
    if frac < 1:                                   # learning curve: keep the first part of each topic's seeds
        keep, seen = [], Counter()
        per = Counter(e.label for e in data.train)
        for e in data.train:
            if seen[e.label] < max(1, round(per[e.label] * frac)):
                keep.append(e); seen[e.label] += 1
        data.train = keep
    ex = ExpertData(CONTENT / "voice" / "experts" / "bone-and-joint-injuries.yaml", a.trees, data) if a.trees else None
    lex = build_lexicon(data.all_text() + (ex.all_text() if ex else []))
    conf = Confusions(lex)
    net = Net(lex, len(data.names), d=a.d, hidden=a.hidden, buckets=a.buckets, teacher=teacher_vectors(a, lex, a.d))
    t0 = time.time()
    train(net, data, lex, conf, a.epochs, a.seed, log=log)
    packed = Packed(net, list(range(len(data.names))), data.names, kind=0)
    res = evaluate(packed, data, lex, conf)
    return data, lex, packed, res, time.time() - t0


def run_expert(a, tree, log=print):
    rdata = RouterData(CONTENT / "voice" / "topics.yaml", a.trees)
    data = ExpertData(CONTENT / "voice" / "experts" / f"{tree}.yaml", a.trees, rdata)
    add_extra(a, data, "expert", tree)
    lex = build_lexicon(rdata.all_text() + data.all_text())
    conf = Confusions(lex)
    net = Net(lex, len(data.names), d=a.d, hidden=a.hidden, buckets=a.buckets, ctx=len(data.nodes),
              teacher=teacher_vectors(a, lex, a.d))
    t0 = time.time()
    train(net, data, lex, conf, a.epochs, a.seed, ctx_fn=data.ctx_for, log=log)
    packed = Packed(net, list(range(len(data.names))), data.names, kind=1)
    inside = [e for e in data.test if e.label != data.none]
    other = [e for e in data.test if e.label == data.none]
    parent = lambda l, r: (data.parents.get(l) or [data.root])[0]
    res_parent = evaluate(packed, data, lex, conf, ctx_mode=parent, same=data.same, test=inside)
    res_root = evaluate(packed, data, lex, conf, ctx_mode=lambda l, r: data.root, same=data.same, test=inside)
    res_none = evaluate(packed, data, lex, conf, ctx_mode=lambda l, r: r.randrange(len(data.nodes)), same=data.same, test=other)
    return data, lex, packed, (res_parent, res_root, res_none), time.time() - t0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vr")
    ap.add_argument("cmd", choices=["router", "expert", "sweep", "curve"])
    ap.add_argument("tree", nargs="?")
    ap.add_argument("--trees", type=Path, required=True, help="directory of tree YAML (draft or signed off)")
    ap.add_argument("--out", type=Path, default=HERE / "out")
    ap.add_argument("--d", type=int, default=64)
    ap.add_argument("--hidden", type=int, default=128)
    ap.add_argument("--buckets", type=int, default=4096)
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--extra", type=Path, help="generated sentences from vr.generate (desktop)")
    ap.add_argument("--teacher", help="sentence-transformers model for starting word vectors (desktop), "
                    "e.g. sentence-transformers/all-MiniLM-L6-v2")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    if a.cmd == "router":
        data, lex, packed, res, secs = run_router(a)
        (a.out / "router.vxp").write_bytes(packed.to_bytes())
        md = report("Router (Home: which topic?)", res, packed, f"{len(data.names) - 1} topics + reject, "
                    f"{len(data.train)} seed sentences, {len(data.test)} held out. Trained in {secs:.0f} s on CPU.")
        (a.out / "router.md").write_text(md); print(md)
        golden(a.out / "router.golden.json", packed, data, lex)
    elif a.cmd == "expert":
        data, lex, packed, (rp, rr, rn), secs = run_expert(a, a.tree)
        (a.out / f"expert-{a.tree}.vxp").write_bytes(packed.to_bytes())
        note = f"{len(data.nodes)} nodes + none, {len(data.train)} seed sentences, {len(data.test)} held out. Trained in {secs:.0f} s."
        md = report(f"Expert {a.tree}, user on the parent card", rp, packed, note) + "\n" + \
            report(f"Expert {a.tree}, user still on the first card (jumping ahead)", rr, packed, "") + "\n" + \
            report(f"Expert {a.tree}, sentence from ANOTHER topic (right answer: hand back to the router)", rn, packed, "")
        (a.out / f"expert-{a.tree}.md").write_text(md); print(md)
        golden(a.out / f"expert-{a.tree}.golden.json", packed, data, lex, ctx=data.root)
    elif a.cmd == "curve":
        rows = ["| seed sentences used | router top-1 clean | top-1 @0.30 | top-3 @0.30 | auto, WRONG @0.30 |", "|---|---|---|---|---|"]
        for f in (0.25, 0.5, 0.75, 1.0):
            a.frac = f
            data, _, packed, res, _ = run_router(a, log=None)
            rows.append(f"| {len(data.train)} ({100 * f:.0f}%) | {100 * res[0.0]['top1']:.0f}% | {100 * res[0.3]['top1']:.0f}% | "
                        f"{100 * res[0.3]['top3']:.0f}% | {100 * res[0.3].get('auto_wrong', 0):.0f}% |")
            print(rows[-1], flush=True)
        (a.out / "curve.md").write_text("\n".join(rows) + "\n")
    else:
        rows = ["| d | hidden | buckets | KB | router top-1 clean | top-1 @0.15 | top-1 @0.30 | top-3 @0.30 |", "|---|---|---|---|---|---|---|---|"]
        for d, h, b in [(16, 32, 1024), (32, 64, 2048), (64, 128, 4096), (96, 192, 8192), (128, 256, 16384)]:
            a.d, a.hidden, a.buckets = d, h, b
            _, _, packed, res, _ = run_router(a, log=None)
            rows.append(f"| {d} | {h} | {b} | {size_of(packed) / 1024:.0f} | {100 * res[0.0]['top1']:.0f}% | "
                        f"{100 * res[0.15]['top1']:.0f}% | {100 * res[0.3]['top1']:.0f}% | {100 * res[0.3]['top3']:.0f}% |")
            print(rows[-1], flush=True)
        (a.out / "sweep.md").write_text("\n".join(rows) + "\n")


def golden(path, packed, data, lex, ctx=-1):
    """A few inputs + the int8 reference scores, for the C reader's host test."""
    cases = []
    for e in data.test[:12]:
        nb = [[(i, 100) for i in lex.encode(e.text)]]
        cases.append({"text": e.text, "ctx": ctx, "nbest": nb, "logits": [float(v) for v in packed.logits(nb, ctx)]})
    path.write_text(json.dumps({"cases": cases, "neg_ids": [i for i in range(len(lex)) if lex.is_neg(i)]}))
    with open(path.with_suffix(".txt"), "w") as f:          # same cases for the C host test
        for c in cases:
            ids = [i for i, _ in c["nbest"][0]]
            f.write(f"{c['ctx']} {len(ids)} {' '.join(map(str, ids))} {len(c['logits'])} "
                    f"{' '.join(f'{v:.6f}' for v in c['logits'])}\n")


if __name__ == "__main__":
    main()
