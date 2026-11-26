"""Stage 3: one shared acoustic trunk + small per-group tails ("expert tails").

The device runs the trunk once per utterance and keeps its int8 output H (the split features).
When the router picks a group (bleeding, bones, ...), the device loads that group's tail from
flash and re-runs ONLY the tail on the same H, then re-decodes. Trunk = stem + the first
SPLIT[size] blocks, frozen (weights, BatchNorm stats and int8 exponents). Tail = the last blocks
+ the head, fine-tuned per group from the base model's own tail.

    python -m fasr tail plan   --train $SP/tts/train.jsonl:6 --dev $SP/tts/dev.jsonl
    python -m fasr tail train  --base firstaid2m --group bleeding --train ...:6 --train ...:4 --dev ... [--hours 1]
    python -m fasr tail score  --base firstaid2m --set $SP/tts/eval.jsonl --set $SP/me_dev/eval.jsonl
    python -m fasr tail export --base firstaid2m --group bleeding [--base-export $WORK/speech/export/<dir>]
    python -m fasr tail all    --base firstaid2m --train ... --dev ... --set ...   # every group: train, score, export

Groups: pipeline/plb/content/voice/tail_groups.yaml. A clip joins a group when its text has one
of the group's words; a clip with a life word (LIFE_CORE) joins EVERY group. Training mixes the
group's clips 50/50 with all clips (general), so a tail stays a full recognizer, just sharper on
its group. Runs: $WORK/speech/runs/<base>-tail-<group>/.

Score (the scoreboard Charlie set: keyword recall, wrong keywords per clip, life-word misses; not
WER) on each eval set's group clips (+ life clips), base vs tail, same decoder. A tail is KEPT only
if it catches more keywords than the base, loses no life word, and adds at most 0.02 wrong keywords
per clip.

Export (EXPT v1, one file per tail, little-endian, 48-byte header then an FQ81 blob):
    "EXPT" u16 version(1) u16 group_id  u8 trunk_id[8]  u16 layer_a  u16 layer_b  i8 e_h  u8 rsv[3]
    u32 fq81_len  char group[16]  u8 rsv[4]
  layer_a..layer_b-1 = the base model's layer indices the tail replaces (layer_b = n_layers).
  e_h = H's exponent (= base layer layer_a-1's e_out = the FQ81 blob's e_in).
  trunk_id = sha256(base model.bin bytes up to the end of the trunk's last array)[:8]; the loader
  must refuse a tail whose trunk_id differs from the loaded base model's.
  The FQ81 blob is the asrpack.py layout (fasr_q8.c fq_open reads it as is) holding only the tail
  layers; its mu/sd are the base's (unused: the tail's input is H, not log-mel).
  H: int8 (C, T/2), the same layout as every layer output in the goldens (out_*), channel-major.
  Goldens per tail: audio int16, feats, x_q, h, every tail layer's out_*, logits, logp, decode.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import yaml

from . import lexicon as lx

WORK = Path(os.environ.get("WORK", "/mnt/data/first-aid-gpu"))
GROUPS = lx.CONTENT / "voice" / "tail_groups.yaml"
SPLIT = {"xs": 6, "s": 9, "m": 10, "l": 12}     # trunk blocks per size: tail = last 4 blocks + head
# strong life words: a miss of one of these is what the scoreboard counts (decode.LIFE_WORDS also
# holds helpers like "not", "lot", "through" that are only life words next to another one)
LIFE_CORE = ["breathing", "breathe", "breath", "unconscious", "unresponsive", "choking", "choke", "bleeding",
             "blood", "spurting", "pumping", "gushing", "squirting", "pulsing", "cpr", "compressions", "pulse",
             "cardiac", "arrest", "epipen", "epinephrine", "anaphylactic", "anaphylaxis", "seizure", "drowning",
             "drowned", "tourniquet", "collapsed"]
EXPT_HDR = 48
KEEP_FALSE_SLACK = 0.02


# ------------------------------------------------------------------ groups and data
def load_groups(path: Path = GROUPS) -> dict[str, set[str]]:
    g = yaml.safe_load(Path(path).read_text())["groups"]
    return {k: {w.lower() for w in v} for k, v in g.items()}


def clip_groups(text: str, groups: dict[str, set[str]]) -> tuple[set[str], bool]:
    """(groups whose words the text has, has a life word)."""
    ws = set(lx.tokenize(text))
    return {g for g, gw in groups.items() if ws & gw}, bool(ws & set(LIFE_CORE))


def _recs(path: Path) -> list[dict]:
    out = []
    for l in Path(path).read_text().splitlines():
        if l.strip():
            r = json.loads(l)
            if not Path(r["wav"]).is_absolute():
                r["wav"] = str((Path(path).parent / r["wav"]).resolve())
            out.append(r)
    return out


def _write(path: Path, recs: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))


def in_group(r: dict, group: str, groups) -> bool:
    gs, life = clip_groups(r.get("text", ""), groups)
    return group in gs or life


def split_sets(specs: list[str], group: str, groups, out: Path) -> list[str]:
    """--train path[:w] list -> the tail's mix: per input set, its group(+life) clips at w/2 and all of
    its clips at w/2 (train.py gives each set a share proportional to its weight)."""
    mix = []
    for i, spec in enumerate(specs):
        path, _, w = spec.partition(":")
        w = float(w or 1)
        recs = _recs(Path(path))
        grp = [r for r in recs if in_group(r, group, groups)]
        tag = f"{i:02d}_{Path(path).parent.name}_{Path(path).stem}"
        if grp:
            _write(out / f"{tag}_group.jsonl", grp)
            mix.append(f"{out / f'{tag}_group.jsonl'}:{w / 2:g}")
        _write(out / f"{tag}_all.jsonl", recs)
        mix.append(f"{out / f'{tag}_all.jsonl'}:{w / 2 if grp else w:g}")
    return mix


def split_dev(dev: Path, group: str, groups, out: Path, min_clips: int = 50) -> Path:
    recs = _recs(dev)
    grp = [r for r in recs if in_group(r, group, groups)]
    if len(grp) < min_clips:
        return dev
    _write(out / "dev_group.jsonl", grp)
    return out / "dev_group.jsonl"


def run_name(base: str, group: str) -> str:
    return f"{base}-tail-{group}"


def base_size(base: str) -> str:
    import torch
    return torch.load(WORK / "speech" / "runs" / base / "best.pt", map_location="cpu").get("size", "s")


# ------------------------------------------------------------------ plan / train
def cmd_plan(a):
    groups = load_groups(a.groups)
    rows = {r["word"] for r in yaml.safe_load(lx.LEXICON.read_text())["words"]}
    for g, ws in groups.items():
        miss = sorted(ws - rows)
        print(f"group {g}: {len(ws)} words" + (f" (not in lexicon, ignored: {' '.join(miss)})" if miss else ""))
    for spec in a.train + ([a.dev] if a.dev else []) + a.set:
        recs = _recs(Path(spec.partition(":")[0]))
        cnt = {g: 0 for g in groups}
        life = 0
        for r in recs:
            gs, lf = clip_groups(r.get("text", ""), groups)
            life += lf
            for g in gs:
                cnt[g] += 1
        print(f"{spec}: {len(recs)} clips, life {life}, " + ", ".join(f"{g} {n}" for g, n in cnt.items()))


def cmd_train(a, extra):
    from . import train
    groups = load_groups(a.groups)
    if a.group not in groups:
        raise SystemExit(f"unknown group {a.group}; groups: {', '.join(groups)}")
    size = base_size(a.base)
    k = a.split if a.split is not None else SPLIT[size]
    run = run_name(a.base, a.group)
    data = WORK / "speech" / "runs" / run / "data"
    mix = split_sets(a.train, a.group, groups, data)
    dev = split_dev(Path(a.dev), a.group, groups, data)
    (WORK / "speech" / "runs" / run / "tail.json").write_text(json.dumps(
        {"base": a.base, "group": a.group, "size": size, "split": k, "mix": mix, "dev": str(dev)}, indent=1))
    argv = ["--run", run, "--init", a.base, "--size", size, "--freeze-trunk", str(k), "--hours", str(a.hours),
            "--lr", str(a.lr), "--dev", str(dev)]
    for m in mix:
        argv += ["--train", m]
    print(f"tail {a.group}: base {a.base} ({size}), trunk = stem + blocks 0-{k - 1}, tail = blocks {k}+ and head")
    return train.main(argv + extra)


# ------------------------------------------------------------------ score
def keyword_ids(rows, groups) -> tuple[set[int], set[int]]:
    """(scored keywords: router slot classes + every group word + life words, life-word ids)"""
    from .evaluate import BLEED
    gw = set().union(*groups.values()) | set(LIFE_CORE) | BLEED
    kw = {r["id"] for r in rows if r.get("class") in (1, 2, 6) or r["word"] in gw}
    life = {r["id"] for r in rows if r["word"] in LIFE_CORE}
    return kw, life


def score_model(model, recs, trie, prm, kw, life) -> dict:
    import torch
    from .data import load_wav
    from .decode import decode
    from .features import logmel
    ids = {}
    for r in yaml.safe_load(lx.LEXICON.read_text())["words"]:
        ids[r["word"]] = r["id"]
    n = ref_kw = hit = false = life_ref = life_miss = 0
    for r in recs:
        f = torch.from_numpy(logmel(load_wav(r["wav"])))[None]
        with torch.no_grad():
            lp = model(f)[0].numpy().astype(np.float64)
        hyp = decode(lp, trie, prm)
        top = set(hyp["h"][0]["ids"]) if hyp["n"] else set()
        any5 = set().union(*[set(h["ids"]) for h in hyp["h"]]) if hyp["n"] else set()
        ref = [ids.get(w, 0) for w in lx.tokenize(r["text"])]
        rk = [w for w in ref if w in kw]
        n += 1; ref_kw += len(rk); hit += sum(w in top for w in rk)
        false += len({w for w in top if w in kw} - set(ref))
        rl = {w for w in ref if w in life}
        life_ref += len(rl); life_miss += len(rl - any5)
    return {"clips": n, "keywords": ref_kw, "keyword_recall": round(hit / max(1, ref_kw), 3),
            "wrong_kw_per_clip": round(false / max(1, n), 3), "life_words": life_ref, "life_missed": life_miss}


def verdict(b: dict, t: dict) -> bool:
    return (t["keyword_recall"] > b["keyword_recall"] and t["life_missed"] <= b["life_missed"]
            and t["wrong_kw_per_clip"] <= b["wrong_kw_per_clip"] + KEEP_FALSE_SLACK)


def cmd_score(a):
    from .decode import Params, Trie, life_bias
    from .evaluate import load_model
    groups = load_groups(a.groups)
    rows = yaml.safe_load(lx.LEXICON.read_text())["words"]
    trie, prm = Trie.from_lexicon(rows), Params(beam=a.beam, bias=life_bias(rows))
    kw, life = keyword_ids(rows, groups)
    base = load_model(a.base)
    todo = [g for g in (a.group or groups) if (WORK / "speech" / "runs" / run_name(a.base, g) / "best.pt").exists()]
    if not todo:
        raise SystemExit("no tail runs yet: fasr tail train first")
    sets = {sp: _recs(Path(sp)) for sp in a.set}
    report, cache = {}, {}
    print(f"{'group':12s} {'set':22s} {'clips':>5s}  {'keywords base/tail':>19s}  {'wrong/clip base/tail':>21s}  "
          f"{'life missed base/tail':>21s}")
    for g in todo:
        tail = load_model(run_name(a.base, g))
        report[g] = {}
        tot_b = {"keyword_recall": 0, "wrong_kw_per_clip": 0, "life_missed": 0}
        keep = True
        for sp, recs in sets.items():
            sub = [r for r in recs if in_group(r, g, groups)][: a.limit or None]
            if not sub:
                continue
            key = (sp, g)
            b = cache[key] = cache.get(key) or score_model(base, sub, trie, prm, kw, life)
            t = score_model(tail, sub, trie, prm, kw, life)
            ok = verdict(b, t)
            keep &= ok
            name = f"{Path(sp).parent.name}/{Path(sp).stem}"
            report[g][name] = {"base": b, "tail": t, "better": ok}
            print(f"{g:12s} {name:22s} {b['clips']:5d}  {b['keyword_recall']:8.1%} /{t['keyword_recall']:7.1%}  "
                  f"{b['wrong_kw_per_clip']:10.2f} /{t['wrong_kw_per_clip']:6.2f}   "
                  f"{b['life_missed']:9d} /{t['life_missed']:5d}  {'better' if ok else 'not better'}", flush=True)
        report[g]["keep"] = keep
        print(f"{g:12s} -> {'KEEP' if keep else 'DROP (base model stays for this group)'}")
    out = WORK / "speech" / "runs" / a.base / "tail_score.json"
    out.write_text(json.dumps(report, indent=1))
    print("saved", out)
    return report


# ------------------------------------------------------------------ export
def trunk_end(meta_layers: list[dict], n_trunk_layers: int) -> int:
    return max(l["e_w_off"] + l["cout"] for l in meta_layers[:n_trunk_layers])


def pack_fq81(meta: dict, blob: bytes) -> bytes:
    """Same bytes as firmware/components/asr/tools/asrpack.py for a model.json + model.bin."""
    L = meta["layers"]
    head = b"FQ81" + struct.pack("<HHhHI", 1, len(L), meta["e_in"], 0, 0)
    head += struct.pack("<40f", *meta["mu"]) + struct.pack("<40f", *meta["sd"])
    for l in L:
        flags = (1 if l["relu"] else 0) | (2 if l["residual"] else 0) | (4 if l["head"] else 0)
        head += struct.pack("<6H3bB3I4x", l["cin"], l["cout"], l["k"], l["stride"], l["pad"], l["groups"],
                            l["e_in"], l["e_out"] if l["e_out"] is not None else 0, l.get("e_res", 0), flags,
                            l["w_off"], l["b_off"], l["e_w_off"])
    off = (len(head) + 15) // 16 * 16
    out = bytearray(head + b"\0" * (off - len(head)))
    struct.pack_into("<I", out, 12, off)
    return bytes(out + blob)


def pack_expt(group_id: int, group: str, trunk_id: bytes, layer_a: int, layer_b: int, e_h: int, fq81: bytes) -> bytes:
    hdr = b"EXPT" + struct.pack("<HH", 1, group_id) + trunk_id + struct.pack("<HHb3xI", layer_a, layer_b, e_h, len(fq81))
    hdr += group.encode()[:16].ljust(16, b"\0") + b"\0" * 4
    assert len(hdr) == EXPT_HDR
    return hdr + fq81


def latest_export(base: str) -> Path | None:
    best = None
    for man in (WORK / "speech" / "export").glob("*/MANIFEST.json"):
        try:
            d = json.loads(man.read_text())
        except (OSError, ValueError):
            continue
        if d.get("run") == base and (best is None or man.stat().st_mtime > best.stat().st_mtime):
            best = man
    return best.parent if best else None


def cmd_export(a):
    import torch
    from .data import load_wav
    from .decode import Params, Trie, decode, life_bias
    from .evaluate import load_model
    from .export_int8 import _commit, fold, forward_f, forward_q, logits_to_logp, quantise, run_q, to_int16, write_bin
    from .features import logmel
    groups = load_groups(a.groups)
    gid = list(groups).index(a.group)
    run = run_name(a.base, a.group)
    info = json.loads((WORK / "speech" / "runs" / run / "tail.json").read_text())
    k = info["split"]
    bexp = Path(a.base_export) if a.base_export else latest_export(a.base)
    if not bexp:
        raise SystemExit(f"no int8 export of {a.base} found: run `fasr export_int8 --run {a.base}` first")
    bmeta = json.loads((bexp / "model.json").read_text())
    bbin = (bexp / "model.bin").read_bytes()
    n_trunk = 1 + 2 * k                       # stem + (dw, pw) per trunk block
    names = [l["name"] for l in bmeta["layers"]]
    assert names[n_trunk - 1] == f"b{k - 1}.pw", names[n_trunk - 1]
    fixed = {"input": bmeta["e_in"], **{l["name"]: l["e_out"] for l in bmeta["layers"][:n_trunk]}}

    base, tail = load_model(a.base), load_model(run)
    bs, ts = base.state_dict(), tail.state_dict()
    for key in bs:
        if key.split(".")[0] in ("stem", "bn0", "mu", "sd") or (key.startswith("blocks.") and int(key.split(".")[1]) < k):
            assert torch.equal(bs[key], ts[key]), f"trunk changed in the tail run: {key}"
    layers = fold(tail)

    cp = Path(a.calib)
    recs = [json.loads(l) for l in cp.read_text().splitlines() if l.strip()]
    random.Random(0).shuffle(recs)
    clips = []
    for r in recs[: a.n_calib + a.n_golden]:
        wav = Path(r["wav"]) if Path(r["wav"]).is_absolute() else cp.parent / r["wav"]
        x = to_int16(load_wav(str(wav)))
        clips.append((x, logmel(x), r.get("text", "")))
    golden, calib = clips[: a.n_golden], clips[a.n_golden:]
    q = quantise(layers, [c[1][:600] for c in calib], fixed=fixed)

    # the trunk must be byte-identical to the base export on the device
    import tempfile
    tq = q["layers"][n_trunk:]
    e_h = q["layers"][n_trunk - 1]["e_out"]
    assert tq[0]["e_in"] == e_h and tq[1]["e_res"] == e_h
    with tempfile.TemporaryDirectory() as td:
        fm = write_bin(q, Path(td))
        fb = (Path(td) / "model.bin").read_bytes()
    end = trunk_end(bmeta["layers"], n_trunk)
    same_e = [l["e_out"] for l in fm["layers"][:n_trunk]] == [l["e_out"] for l in bmeta["layers"][:n_trunk]]
    if fb[:end] != bbin[:end] or not same_e:
        raise SystemExit(f"trunk bytes differ from the base export {bexp}: export the base again from this commit")
    trunk_id = hashlib.sha256(bbin[:end]).digest()[:8]

    commit = _commit()
    out = Path(a.out) if a.out else WORK / "speech" / "export" / "tails" / bexp.name / a.group
    (out / "golden").mkdir(parents=True, exist_ok=True)
    tqd = {"e_in": e_h, "layers": tq, "mu": q["mu"], "sd": q["sd"]}
    tmeta = write_bin(tqd, out)              # tail-only model.bin (offsets from 0)
    tmeta.update(format="FASR-Q8 v1 tail", e_in=e_h, mu=q["mu"].tolist(), sd=q["sd"].tolist(), commit=commit, run=run,
                 base_run=a.base, base_export=bexp.name, group=a.group, group_id=gid, split_block=k,
                 layer_a=n_trunk, layer_b=len(q["layers"]), trunk_id=trunk_id.hex())
    (out / "model.json").write_text(json.dumps(tmeta, indent=1))
    fq = pack_fq81(tmeta, (out / "model.bin").read_bytes())
    (out / "tail_expt.bin").write_bytes(pack_expt(gid, a.group, trunk_id, n_trunk, len(q["layers"]), e_h, fq))

    rows = yaml.safe_load(lx.LEXICON.read_text())["words"]
    trie, params = Trie.from_lexicon(rows), Params(bias=life_bias(rows))
    agree = tot = 0
    for i, (x, f, text) in enumerate(golden):
        x_q, outs, acc = forward_q(q, f)                        # whole model with this tail
        h = outs[f"b{k - 1}.pw"]
        touts, tacc = run_q(tq, h)                              # tail alone from H: must be identical
        assert np.array_equal(tacc, acc), "tail from H differs from the whole model"
        lpq = logits_to_logp(q, acc)
        hyp = decode(lpq.astype(np.float64), trie, params)
        np.savez(out / "golden" / f"{i:02d}.npz", audio=x, feats=f, x_q=x_q, h=h, logits=acc, logp=lpq,
                 decode=json.dumps(hyp), text=text, **{f"out_{n.replace('.', '_')}": v for n, v in touts.items()})
    for _, f, _ in calib[:50]:
        _, _, acc = forward_q(q, f)
        agree += int((logits_to_logp(q, acc).argmax(1) == forward_f(layers, f).T.argmax(1)).sum()); tot += acc.shape[1]
    man = dict(commit=commit, run=run, base_export=bexp.name, group=a.group, group_id=gid, trunk_id=trunk_id.hex(),
               split_block=k, layer_a=n_trunk, layer_b=len(q["layers"]), e_h=e_h, tail_bin_bytes=tmeta["bytes"],
               expt_bytes=(out / "tail_expt.bin").stat().st_size, golden=len(golden),
               int8_vs_float_greedy_agreement=round(agree / max(1, tot), 4),
               files={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir()) if p.is_file()})
    (out / "MANIFEST.json").write_text(json.dumps(man, indent=1))
    print(json.dumps({k2: v for k2, v in man.items() if k2 != "files"}, indent=1))
    print("tail export:", out)
    return out


# ------------------------------------------------------------------ all
def cmd_all(a, extra):
    groups = load_groups(a.groups)
    for g in (a.group or list(groups)):
        ns = argparse.Namespace(**{**vars(a), "group": g})
        cmd_train(ns, extra)
    rep = cmd_score(argparse.Namespace(**{**vars(a), "group": a.group}))
    for g, r in rep.items():
        if a.export_all or r.get("keep"):
            try:
                cmd_export(argparse.Namespace(**{**vars(a), "group": g, "out": None}))
            except SystemExit as e:
                print(f"export {g} skipped: {e}")
    kept = [g for g, r in rep.items() if r.get("keep")]
    print(f"tails kept: {', '.join(kept) or 'none'} (of {len(rep)})")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fasr tail")
    ap.add_argument("cmd", choices=["plan", "train", "score", "export", "all"])
    ap.add_argument("--base", default="firstaid2m", help="the device base run (its trunk is shared)")
    ap.add_argument("--group", action="append", help="train/export: the group; score/all: limit to these")
    ap.add_argument("--groups", type=Path, default=GROUPS)
    ap.add_argument("--split", type=int, help="trunk blocks (default by size: " + str(SPLIT) + ")")
    ap.add_argument("--train", action="append", default=[], help="manifest[:weight], as for fasr train")
    ap.add_argument("--dev")
    ap.add_argument("--set", action="append", default=[], help="eval manifest(s) for score")
    ap.add_argument("--hours", type=float, default=1.0)
    ap.add_argument("--lr", type=float, default=5e-4)
    ap.add_argument("--beam", type=int, default=24)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--base-export", help="the base model's int8 export dir (default: newest for --base)")
    ap.add_argument("--calib", default=str(WORK / "speech" / "tts" / "dev.jsonl"))
    ap.add_argument("--n-calib", type=int, default=200)
    ap.add_argument("--n-golden", type=int, default=4)
    ap.add_argument("--out")
    ap.add_argument("--export-all", action="store_true", help="all: export every tail, not only kept ones")
    a, extra = ap.parse_known_args(argv)
    if a.cmd in ("train", "export"):
        if not a.group or len(a.group) != 1:
            raise SystemExit(f"tail {a.cmd} needs exactly one --group")
        a.group = a.group[0]
    if a.cmd == "plan":
        return cmd_plan(a)
    if a.cmd == "train":
        return cmd_train(a, extra)
    if extra and a.cmd != "all":
        raise SystemExit(f"unknown arguments: {' '.join(extra)}")
    if a.cmd == "score":
        return cmd_score(a)
    if a.cmd == "export":
        return cmd_export(a)
    return cmd_all(a, extra)


if __name__ == "__main__":
    sys.exit(main())
