"""Stage 2 benchmark (firmware/docs/EXPERT_BANKS_SPEC.md): fill the blanks from the Pass-1 log-prob grid of each
labelled recording, four ways, all with the board's own C code (bankbench.c) where the board would use it:

  P1  Pass 1 as today: the global lexicon's top guess
  2A  re-decode the SAME logp with the narrow lexicon of the 1-2 trees Pass 1 picked (fasr sector_trie words:
      the trees' slot + topic words, ALWAYS_WORDS, LIFE_WORDS, ids 0-9)
  2C  re-decode with the global lexicon + a bias on those trees' words (fasr context_bias: nothing forbidden)
  2B  spot each slot value's phrases straight on the logp grid (CTC keyword path vs a free-phone garbage path,
      threshold set on the train split; a negation word just before a hit cancels it)

Truth = the blanks the form engine fills from the LABEL text in the trees it picks from the label. Scored as
(slot, value) pairs: recall, false fills, life-pair recall; plus tree-gate recall and timing.

  python bankbench.py --bench <bankbench binary> --cart <voice cart> --fasr <pipeline/speech> --lexicon <yaml>
                      --slots <snapshot slots dir> --work <scratch dir> [--split eval]
The work dir needs pass1.jsonl + logp/ from `bankbench pass1` and split.txt (train/eval per line). Personal data
(recordings, labels, slots) stays in the work dir; nothing here is committed with it."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import yaml


def words_of(x, out):
    if isinstance(x, str): out.update(re.findall(r"[a-z']+", x.lower()))
    elif isinstance(x, dict):
        for k, v in x.items(): words_of(k, out); words_of(v, out)
    elif isinstance(x, list):
        for v in x: words_of(v, out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    for k in ("bench", "cart", "fasr", "lexicon", "slots", "work"): ap.add_argument("--" + k, required=True)
    ap.add_argument("--split", default="eval")
    a = ap.parse_args(argv)
    W = Path(a.work); sys.path.insert(0, a.fasr)
    from fasr.decode import ALWAYS_WORDS, LIFE_WORDS
    from fasr.phones import TOK, SEP, BLANK
    rows = yaml.safe_load(Path(a.lexicon).read_text())["words"]
    lexw = {r["word"]: r for r in rows}
    p1 = [json.loads(l) for l in open(W / "pass1.jsonl")]
    split = Path(W / "split.txt").read_text().split()
    # ---- per-tree banks (words) and slot phrases, from the slot snapshot
    tw, phrases, life_pairs = {}, {}, set()
    for f in sorted(Path(a.slots).glob("*.yaml")):
        d = yaml.safe_load(f.read_text()) or {}
        s = set(); words_of(d.get("slots", {}), s); words_of(d.get("topic_words", []), s)
        tw[f.stem] = {w for w in s if w in lexw}
        ph = []
        for slot, vals in (d.get("slots") or {}).items():
            if not isinstance(vals, dict): continue
            for val, plist in vals.items():
                for p in (plist if isinstance(plist, list) else []):
                    ws = re.findall(r"[a-z']+", str(p).lower())
                    if ws and all(w in lexw and lexw[w].get("phones") for w in ws): ph.append((slot, str(val), tuple(ws)))
        phrases[f.stem] = sorted(set(ph))
        for r in d.get("life_threat") or []:
            for k, v in (r.get("if") or {}).items(): life_pairs.add((k, str(v)))
    core = set(ALWAYS_WORDS) | set(LIFE_WORDS)

    def run(args, lines):
        r = subprocess.run(args, input="".join(l + "\n" for l in lines), capture_output=True, text=True)
        return [json.loads(x) if x.strip() else {} for x in r.stdout.splitlines()]

    # ---- truth / P1 fills
    def fills(pairs_by_clip):           # [(i, tree, text)] -> {(i): set((slot, val))}
        out = defaultdict(set)
        res = run([a.bench, "fill", a.cart], [f"{t}\t{x}" for _, t, x in pairs_by_clip])
        for (i, _, _), r in zip(pairs_by_clip, res):
            for s, v in r: out[i].add((s, v))
        return out
    idx = [c["i"] for c in p1 if split[c["i"]] == a.split]
    truth = fills([(c["i"], t, c["label"]) for c in p1 if c["i"] in idx for t in c["oracle_trees"][:2]])
    gate = {c["i"]: c["trees"][:2] for c in p1}
    top = {c["i"]: (c["nbest"][0] if c["nbest"] else "") for c in p1}
    P = {"P1": fills([(i, t, top[i]) for i in idx for t in gate[i]])}

    # ---- narrow / biased lexicons, cached by word set
    lexdir = W / "lexi"; lexdir.mkdir(exist_ok=True)
    def lexi_for(trees, mode):
        words = set().union(*(tw.get(t, set()) for t in trees)) | core
        key = hashlib.sha1((mode + "|" + " ".join(sorted(words))).encode()).hexdigest()[:12]
        out = lexdir / f"{mode}-{key}.bin"
        if out.exists(): return out
        if mode == "narrow":
            sub = [r for r in rows if r["id"] < 10 or r["word"] in words]
            y = lexdir / f"{key}.yaml"; y.write_text(yaml.safe_dump({"version": 1, "cap": 4096, "words": sub}))
            subprocess.run([sys.executable, str(Path(__file__).parents[2] / "components/asr/tools/lexpack.py"),
                            "--fasr", a.fasr, "--lexicon", str(y), "-o", str(out)], check=True, capture_output=True)
        else:                            # global lexicon, bias 0.5 on the trees' words (life keeps 1.0): fasr context_bias
            base = lexdir / "global.bin"
            if not base.exists():
                subprocess.run([sys.executable, str(Path(__file__).parents[2] / "components/asr/tools/lexpack.py"),
                                "--fasr", a.fasr, "--lexicon", a.lexicon, "-o", str(base)], check=True, capture_output=True)
            b = bytearray(base.read_bytes())
            off_bias = struct.unpack_from("<I", b, 88 + 12)[0]
            for r in rows:
                if r["word"] in words:
                    o = off_bias + 8 * r["id"]
                    struct.pack_into("<d", b, o, max(struct.unpack_from("<d", b, o)[0], 0.5))
            out.write_bytes(bytes(b))
        return out
    asrc = W / "asrc.bin"
    for mode, name in (("narrow", "2A"), ("bias", "2C")):
        jobs = defaultdict(list)
        for i in idx:
            if gate[i]: jobs[lexi_for(gate[i], mode)].append(i)
        text, ms = {i: top[i] for i in idx}, []
        for lx, ids in jobs.items():
            res = run([a.bench, "decode", str(lx)] + ([str(asrc)] if asrc.exists() else []), [str(W / "logp" / f"{i}.logp") for i in ids])
            for i, r in zip(ids, res):
                text[i] = r["nbest"][0] if r.get("nbest") else ""; ms.append(r.get("ms", 0))
        P[name] = fills([(i, t, text[i]) for i in idx for t in gate[i]])
        P[name + "_ms"] = float(np.mean(ms)) if ms else 0.0

    # ---- 2B: phrase spotting on the grid
    C = len(TOK)
    def seq(ws):
        out = []
        for k, w in enumerate(ws):
            if k: out.append(SEP)
            out += [TOK[x] for x in lexw[w]["phones"][0].split()]
        return out
    NEG = [w for w in ("not", "no", "can't", "isn't", "don't", "didn't", "doesn't", "won't", "never", "without") if w in lexw]
    def spot(lp, seqs):                  # best normalized CTC path score per phrase, + its start frame
        T = lp.shape[0]; garb = lp.max(1)
        res = []
        for s in seqs:
            ext = [BLANK]
            for t in s: ext += [t, BLANK]
            S = len(ext); NEGINF = -1e9
            dp = np.full(S, NEGINF); st = np.zeros(S, int); best, bstart, bend = NEGINF, 0, 0
            for t in range(T):
                e = lp[t, ext]
                prev = dp.copy(); pst = st.copy()
                stay = prev; adv = np.concatenate(([NEGINF], prev[:-1])); skip = np.full(S, NEGINF)
                skip[2:] = np.where(np.array(ext[2:]) != np.array(ext[:-2]), prev[:-2], NEGINF)
                cand = np.stack([stay, adv, skip]); arg = cand.argmax(0); dp = cand.max(0) + e
                st = np.where(arg == 0, pst, np.where(arg == 1, np.concatenate(([0], pst[:-1])), np.concatenate(([0, 0], pst[:-2]))))
                for k0 in (0, 1):        # a phrase may start at any frame
                    v = e[k0]
                    if v > dp[k0]: dp[k0] = v; st[k0] = t
                for k1 in (S - 1, S - 2):
                    if dp[k1] > NEGINF / 2:
                        n = t - st[k1] + 1
                        sc = (dp[k1] - garb[st[k1]:t + 1].sum()) / n
                        if sc > best: best, bstart, bend = sc, st[k1], t
            res.append((best, bstart, bend))
        return res
    def run_2b(thr, ids):
        out = defaultdict(set)
        for i in ids:
            if not gate[i]: continue
            lp = np.fromfile(W / "logp" / f"{i}.logp", np.float32).reshape(-1, C)
            negs = [r for r in spot(lp, [seq((w,)) for w in NEG]) if r[0] > thr]
            best = {}
            for t in gate[i]:
                for (slot, val, ws), (sc, s0, s1) in zip(phrases[t], spot(lp, [seq(ws) for _, _, ws in phrases[t]])):
                    if sc <= thr: continue
                    if ws[0] not in NEG and any(n1 < s0 and s0 - n1 <= 50 for _, n0, n1 in negs): continue
                    if slot not in best or sc > best[slot][0]: best[slot] = (sc, val)
            for slot, (_, val) in best.items(): out[i].add((slot, val))
        return out
    # threshold on a train sample (cheap grid), then the chosen split
    cache = W / "thr2b.json"
    if cache.exists(): thr = json.loads(cache.read_text())["thr"]
    else:
        tr = [c["i"] for c in p1 if split[c["i"]] == "train"][:120]
        ttruth = fills([(c["i"], t, c["label"]) for c in p1 if c["i"] in tr for t in c["oracle_trees"][:2]])
        bestf, thr = -1, -0.5
        for th in (-1.5, -1.0, -0.75, -0.5, -0.35, -0.25, -0.15):
            pr = run_2b(th, tr); tp = sum(len(pr[i] & ttruth[i]) for i in tr); fp = sum(len(pr[i] - ttruth[i]) for i in tr)
            fn = sum(len(ttruth[i] - pr[i]) for i in tr); f1 = 2 * tp / max(1, 2 * tp + fp + fn)
            if f1 > bestf: bestf, thr = f1, th
        cache.write_text(json.dumps({"thr": thr, "f1_train": bestf}))
    import time; t0 = time.time()
    P["2B"] = run_2b(thr, idx); P["2B_ms"] = (time.time() - t0) * 1000 / max(1, len(idx))

    # ---- score
    print(f"split {a.split}: {len(idx)} clips, {sum(len(truth[i]) for i in idx)} true blanks "
          f"({sum(len([p for p in truth[i] if p in life_pairs]) for i in idx)} life), gate: oracle tree in Pass-1 top 2 "
          f"{sum(1 for c in p1 if c['i'] in idx and c['oracle_trees'] and c['oracle_trees'][0] in gate[c['i']])}"
          f"/{sum(1 for c in p1 if c['i'] in idx and c['oracle_trees'])}")
    for name in ("P1", "2A", "2C", "2B"):
        pr = P[name]
        tp = sum(len(pr[i] & truth[i]) for i in idx); fp = sum(len(pr[i] - truth[i]) for i in idx)
        fn = sum(len(truth[i] - pr[i]) for i in idx)
        lt = sum(len({p for p in truth[i] if p in life_pairs} & pr[i]) for i in idx)
        ln = sum(len({p for p in truth[i] if p in life_pairs}) for i in idx)
        extra = f", {P.get(name + '_ms', 0):.1f} ms/clip PC" if name != "P1" else ""
        print(f"  {name}: recall {tp}/{tp + fn} = {tp / max(1, tp + fn):.1%}, false fills {fp}, life {lt}/{ln}{extra}")
    if "thr" in locals(): print(f"  2B threshold {thr}")


if __name__ == "__main__":
    main()
