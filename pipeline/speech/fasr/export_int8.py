"""Int8 export for the on-device engines (DIY C first, ESP-DL for comparison) + golden files.

    python -m fasr export_int8 --run firstaid            # -> $WORK/speech/export/<commit>/

Everything in one folder, all from ONE git commit, so the firmware thread can drop it in:

  model.bin        weights, little-endian, layout in model.json (format "FASR-Q8 v1")
  model.json       layer list: shapes, exponents, offsets into model.bin; front-end constants
  model_c.h        the same as a C header (const arrays, 16-byte aligned) for the DIY engine
  model.onnx       float torch model (unfolded, input-normalised) for the ESP-DL path (esp-ppq quantises it there)
  lexicon.yaml     the decoder's word list (ids, phones)
  reject_calib.json  calibrated reject (canonical reject; see decode.decode)
  golden/NN.npz    per clip: audio int16 -> feats (float) -> x_q (int8 input) -> every layer's
                   int8 output -> logits int32 -> logp float -> decode() result (json)
  MANIFEST.json    commit, run, size, int8-vs-float agreement, file hashes

Quantisation ("DIY friendly", bit-exact in integers):
  * BatchNorm is folded into the convs. The input is normalised in float first,
    x = (logmel - mu) / sd  (mu, sd per mel band in model.json / model_c.h), then quantised
    x_q = clamp(round(x * 2^e_in)): not folded, because the causal zero padding is in the
    normalised domain.
  * Every tensor uses POWER-OF-TWO scales: real = q * 2^-e. Weights: int8 per output channel
    (exponent e_w[c]); activations: int8 per tensor (e_a); biases int32 at e_in + e_w[c].
  * Conv: acc(int32) = bias + sum(x_q * w_q);  y_q = clamp(rshift_round(acc, e_in + e_w[c] - e_out))
    with rshift_round(v, s) = (v + (1 << (s-1))) >> s  (arithmetic shift; s <= 0 means v << -s).
  * Residual add: both sides shifted to the output exponent the same way, summed in int32, then
    ReLU + clamp to [0, 127].  ReLU layers clamp to [0, 127], others to [-128, 127].
  * Head: int32 logits at e_in + e_w[c]; the device converts to float and does log-softmax.
  * Causal convs pad on the LEFT with zeros (q = 0). Stem: k 5, stride 2, left pad 3.
The numpy integer forward here IS the reference: the C port must match golden/*.npz exactly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
from pathlib import Path

import numpy as np
import torch
import yaml

from . import lexicon as lx
from .features import FMAX, FMIN, HOP, NFFT, NMEL, SR, WIN, logmel

WORK = Path(os.environ.get("WORK", "/mnt/data/first-aid-gpu"))
FORMAT = "FASR-Q8 v1"


# ------------------------------------------------------------------ folding (float)
def _fold_bn(w: np.ndarray, b: np.ndarray, bn) -> tuple[np.ndarray, np.ndarray]:
    g = (bn.weight / torch.sqrt(bn.running_var + bn.eps)).detach().numpy()
    beta = (bn.bias - bn.running_mean * bn.weight / torch.sqrt(bn.running_var + bn.eps)).detach().numpy()
    return w * g[:, None, None], b * g + beta


def fold(m) -> list[dict]:
    """Float layer list with BN + input norm folded. Each: kind, w (cout, cin/groups, k), b, k,
    stride, pad, groups, relu, and for 'block' the pair dw/pw."""
    m = m.eval()
    if float(m.film.abs().sum()) != 0.0:
        raise SystemExit("this run has a speaker profile (film) loaded: export the base model")
    mu, sd = m.mu.numpy().astype(np.float64), m.sd.numpy().astype(np.float64)
    w = m.stem.conv.weight.detach().numpy().astype(np.float64)        # (c, 40, 5)
    w, b = _fold_bn(w, np.zeros(w.shape[0]), m.bn0)
    layers = [dict(name="stem", kind="conv", w=w, b=b, k=5, stride=2, pad=m.stem.pad, groups=1, relu=True,
                   mu=mu, sd=sd)]
    for i, blk in enumerate(m.blocks):
        dw = blk.dw.conv.weight.detach().numpy().astype(np.float64)
        dw, dwb = _fold_bn(dw, np.zeros(dw.shape[0]), blk.bn1)
        pw = blk.pw.weight.detach().numpy().astype(np.float64)
        pw, pwb = _fold_bn(pw, np.zeros(pw.shape[0]), blk.bn2)
        layers.append(dict(name=f"b{i}.dw", kind="conv", w=dw, b=dwb, k=dw.shape[2], stride=1, pad=blk.dw.pad,
                           groups=dw.shape[0], relu=True))
        layers.append(dict(name=f"b{i}.pw", kind="conv", w=pw, b=pwb, k=1, stride=1, pad=0, groups=1, relu=False,
                           residual=True))   # out = relu(block_in + pw)
    hw = m.head.weight.detach().numpy().astype(np.float64)
    hb = (m.head.bias + m.head_bias).detach().numpy().astype(np.float64)
    layers.append(dict(name="head", kind="conv", w=hw, b=hb, k=1, stride=1, pad=0, groups=1, relu=False, head=True))
    return layers


def conv_f(x: np.ndarray, L: dict) -> np.ndarray:
    """float reference conv on (C, T)."""
    w, k, s, g = L["w"], L["k"], L["stride"], L["groups"]
    x = np.pad(x, ((0, 0), (L["pad"], 0)))
    T = (x.shape[1] - k) // s + 1
    cols = np.stack([x[:, i * s:i * s + k] for i in range(T)], 1)      # (Cin, T, k)
    if g == 1:
        y = np.einsum("oik,itk->ot", w, cols)
    else:
        y = np.einsum("ok,otk->ot", w[:, 0, :], cols)
    return y + L["b"][:, None]


def norm(layers, feats):
    return ((feats.astype(np.float64) - layers[0]["mu"]) / layers[0]["sd"]).T


def forward_f(layers, feats: np.ndarray, keep=False):
    x = norm(layers, feats)
    acts = {"input": x}
    blk_in = None
    for L in layers:
        if L["name"].endswith(".dw"):
            blk_in = x
        y = conv_f(x, L)
        if L.get("residual"):
            y = np.maximum(blk_in + y, 0)
        elif L["relu"]:
            y = np.maximum(y, 0)
        acts[L["name"]] = y
        x = y
    return (x, acts) if keep else x


# ------------------------------------------------------------------ quantisation
def _exp_for(maxabs: float, bits=7) -> int:
    """largest e with maxabs * 2^e <= 2^bits - 1  (real = q * 2^-e)."""
    if maxabs <= 0:
        return 0
    return int(np.floor(np.log2((2 ** bits - 1) / maxabs)))


def rshift_round(v: np.ndarray, s) -> np.ndarray:
    s = np.asarray(s, np.int64)
    v = v.astype(np.int64)
    pos = np.where(s > 0, (v + (np.int64(1) << np.maximum(s - 1, 0))) >> np.maximum(s, 0), v)
    return np.where(s < 0, v << np.maximum(-s, 0), pos)


def quantise(layers, calib_feats: list[np.ndarray], pct=99.99) -> dict:
    """Activation exponents from float activations on calibration clips, weights per channel."""
    peaks: dict[str, list] = {}
    for f in calib_feats:
        _, acts = forward_f(layers, f, keep=True)
        for k, v in acts.items():
            peaks.setdefault(k, []).append(np.percentile(np.abs(v), pct))
    e_act = {k: _exp_for(float(np.max(v))) for k, v in peaks.items()}
    q = {"e_in": e_act["input"], "layers": [], "mu": layers[0]["mu"], "sd": layers[0]["sd"]}
    e_prev = e_act["input"]
    for L in layers:
        w = L["w"]
        e_w = np.array([_exp_for(float(np.abs(w[c]).max())) for c in range(w.shape[0])], np.int64)
        e_w = np.minimum(e_w, 15)
        wq = np.clip(np.round(w * (2.0 ** e_w)[:, None, None]), -127, 127).astype(np.int8)
        e_acc = e_prev + e_w
        bq = np.round(L["b"] * 2.0 ** e_acc).astype(np.int64)
        assert np.abs(bq).max() < 2 ** 31, L["name"]
        e_out = None if L.get("head") else e_act[L["name"]]
        ql = dict(name=L["name"], k=L["k"], stride=L["stride"], pad=L["pad"], groups=L["groups"], relu=bool(L["relu"]),
                  residual=bool(L.get("residual")), head=bool(L.get("head")), cin=int(w.shape[1] * L["groups"]),
                  cout=int(w.shape[0]), wq=wq, bq=bq.astype(np.int32), e_w=e_w.astype(np.int8), e_in=int(e_prev),
                  e_out=None if e_out is None else int(e_out))
        if L.get("residual"):
            ql["e_res"] = int(q["layers"][-2]["e_out"]) if len(q["layers"]) >= 2 else int(q["e_in"])
        q["layers"].append(ql)
        if e_out is not None:
            e_prev = e_out
    return q


def conv_q(x: np.ndarray, Q: dict) -> np.ndarray:
    """int8 (Cin, T) -> int32 accumulators (Cout, T')."""
    w, k, s, g = Q["wq"].astype(np.int64), Q["k"], Q["stride"], Q["groups"]
    x = np.pad(x.astype(np.int64), ((0, 0), (Q["pad"], 0)))
    T = (x.shape[1] - k) // s + 1
    cols = np.stack([x[:, i * s:i * s + k] for i in range(T)], 1)
    acc = np.einsum("oik,itk->ot", w, cols) if g == 1 else np.einsum("ok,otk->ot", w[:, 0, :], cols)
    return acc + Q["bq"].astype(np.int64)[:, None]


def forward_q(q: dict, feats: np.ndarray):
    """The bit-exact integer reference. Returns (x_q input, {layer: int8 out}, logits int32)."""
    xn = ((feats.astype(np.float64) - q["mu"]) / q["sd"]).T
    x = np.clip(np.round(xn * 2.0 ** q["e_in"]), -128, 127).astype(np.int8)
    x_in, outs, blk_in, e_blk = x, {}, None, None
    for Q in q["layers"]:
        if Q["name"].endswith(".dw"):
            blk_in, e_blk = x, Q["e_in"]
        acc = conv_q(x, Q)
        if Q["head"]:
            return x_in, outs, acc.astype(np.int32)
        shift = (Q["e_in"] + Q["e_w"].astype(np.int64))[:, None] - Q["e_out"]
        y = rshift_round(acc, shift)
        if Q["residual"]:
            y = y + rshift_round(blk_in.astype(np.int64), e_blk - Q["e_out"])
        lo = 0 if (Q["relu"] or Q["residual"]) else -128
        x = np.clip(y, lo, 127).astype(np.int8)
        outs[Q["name"]] = x
    raise RuntimeError("no head layer")


def logits_to_logp(q: dict, acc: np.ndarray) -> np.ndarray:
    H = q["layers"][-1]
    z = acc.astype(np.float64) * 2.0 ** -(H["e_in"] + H["e_w"].astype(np.float64))[:, None]
    z = z.T
    z = z - z.max(1, keepdims=True)
    return (z - np.log(np.exp(z).sum(1, keepdims=True))).astype(np.float32)


# ------------------------------------------------------------------ writing
def write_bin(q: dict, out: Path) -> dict:
    """model.bin: each array 16-byte aligned (PIE SIMD loads); offsets in model.json."""
    blob = bytearray()
    meta = []

    def put(a: np.ndarray) -> int:
        while len(blob) % 16:
            blob.append(0)
        off = len(blob)
        blob.extend(np.ascontiguousarray(a).tobytes())
        return off

    for Q in q["layers"]:
        d = {k: v for k, v in Q.items() if k not in ("wq", "bq", "e_w")}
        d["w_off"], d["w_shape"] = put(Q["wq"]), list(Q["wq"].shape)
        d["b_off"] = put(Q["bq"].astype("<i4"))
        d["e_w_off"] = put(Q["e_w"])
        meta.append(d)
    (out / "model.bin").write_bytes(bytes(blob))
    return {"layers": meta, "bytes": len(blob)}


def write_header(q: dict, out: Path, commit: str):
    lines = [f"/* GENERATED by `python -m fasr export_int8` at commit {commit}. {FORMAT}. Do not edit. */",
             "#pragma once", "#include <stdint.h>", f"#define FASR_E_IN {q['e_in']}",
             f"#define FASR_N_LAYERS {len(q['layers'])}",
             "static const float FASR_MU[40] = {" + ",".join(f"{float(v):.9e}f" for v in q["mu"]) + "};",
             "static const float FASR_SD[40] = {" + ",".join(f"{float(v):.9e}f" for v in q["sd"]) + "};", ""]
    for i, Q in enumerate(q["layers"]):
        n = Q["name"].replace(".", "_")
        lines.append(f"static const int8_t {n}_w[{Q['wq'].size}] __attribute__((aligned(16))) = {{"
                     + ",".join(map(str, Q["wq"].ravel().tolist())) + "};")
        lines.append(f"static const int32_t {n}_b[{Q['bq'].size}] __attribute__((aligned(16))) = {{"
                     + ",".join(map(str, Q["bq"].tolist())) + "};")
        lines.append(f"static const int8_t {n}_ew[{Q['e_w'].size}] = {{" + ",".join(map(str, Q["e_w"].tolist())) + "};")
    lines.append("typedef struct { const int8_t *w; const int32_t *b; const int8_t *ew; int16_t cin, cout, k, stride, pad, groups;"
                 " int8_t e_in, e_out, e_res; uint8_t relu, residual, head; } fasr_layer_t;")
    lines.append("static const fasr_layer_t FASR_LAYERS[FASR_N_LAYERS] = {")
    for Q in q["layers"]:
        n = Q["name"].replace(".", "_")
        lines.append(f"  {{{n}_w, {n}_b, {n}_ew, {Q['cin']}, {Q['cout']}, {Q['k']}, {Q['stride']}, {Q['pad']}, {Q['groups']}, "
                     f"{Q['e_in']}, {Q['e_out'] if Q['e_out'] is not None else 0}, {Q.get('e_res', 0)}, "
                     f"{int(Q['relu'])}, {int(Q['residual'])}, {int(Q['head'])}}},")
    lines.append("};")
    (out / "model_c.h").write_text("\n".join(lines) + "\n")


def _commit() -> str:
    try:
        root = Path(__file__).resolve().parents[3]
        c = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", "pipeline/speech", "pipeline/plb/content/lexicon.yaml"],
                               capture_output=True, text=True).stdout.strip()
        return (c or "nogit") + ("-dirty" if dirty else "")
    except OSError:
        return "nogit"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fasr export_int8")
    ap.add_argument("--run", required=True)
    ap.add_argument("--calib", default=str(WORK / "speech" / "tts" / "dev.jsonl"), help="public synthetic clips only")
    ap.add_argument("--n-calib", type=int, default=200)
    ap.add_argument("--n-golden", type=int, default=8)
    ap.add_argument("--out", default=None, help="default $WORK/speech/export/<commit>")
    a = ap.parse_args(argv)
    from .data import load_wav
    from .decode import Params, Trie, decode, life_bias
    from .evaluate import load_model

    commit = _commit()
    out = Path(a.out) if a.out else WORK / "speech" / "export" / commit
    (out / "golden").mkdir(parents=True, exist_ok=True)
    m = load_model(a.run).eval()
    layers = fold(m)

    cp = Path(a.calib)
    recs = [json.loads(l) for l in cp.read_text().splitlines() if l.strip()]
    random.Random(0).shuffle(recs)
    clips = []
    for r in recs[: a.n_calib + a.n_golden]:
        wav = Path(r["wav"]) if Path(r["wav"]).is_absolute() else cp.parent / r["wav"]
        x = load_wav(str(wav))
        clips.append((x, logmel(x), r.get("text", "")))
    golden, calib = clips[: a.n_golden], clips[a.n_golden:]

    # folded float must equal the torch model
    with torch.no_grad():
        ref = m(torch.from_numpy(calib[0][1])[None])[0].numpy()
    z = forward_f(layers, calib[0][1]).T
    lp = z - z.max(1, keepdims=True); lp = lp - np.log(np.exp(lp).sum(1, keepdims=True))
    fold_err = float(np.abs(lp - ref).max())
    assert fold_err < 1e-3, f"BN folding mismatch {fold_err}"

    q = quantise(layers, [c[1][:600] for c in calib])
    meta = write_bin(q, out)
    meta.update(format=FORMAT, e_in=q["e_in"], mu=q["mu"].tolist(), sd=q["sd"].tolist(), commit=commit, run=a.run, frontend=dict(
        sr=SR, win=WIN, hop=HOP, nfft=NFFT, nmel=NMEL, fmin=FMIN, fmax=FMAX, window="hann(periodic, N+1 drop last)",
        log="ln(power + 1e-6)", out_stride=2, note="pipeline/speech/fasr/features.py is the reference"))
    (out / "model.json").write_text(json.dumps(meta, indent=1))
    write_header(q, out, commit)

    # agreement int8 vs float on calibration clips (greedy tokens)
    agree, tot = 0, 0
    for _, f, _ in calib[:50]:
        _, _, acc = forward_q(q, f)
        a8 = logits_to_logp(q, acc).argmax(1)
        af = forward_f(layers, f).T.argmax(1)
        agree += int((a8 == af).sum()); tot += len(af)

    rows = yaml.safe_load(lx.LEXICON.read_text())["words"]
    trie, params = Trie.from_lexicon(rows), Params(bias=life_bias(rows))
    cal_f = WORK / "speech" / "runs" / a.run / "reject_calib.json"
    calib_json = json.loads(cal_f.read_text()) if cal_f.exists() else None
    for i, (x, f, text) in enumerate(golden):
        x_q, outs, acc = forward_q(q, f)
        lpq = logits_to_logp(q, acc)
        hyp = decode(lpq.astype(np.float64), trie, params, calib_json)
        np.savez(out / "golden" / f"{i:02d}.npz", audio=(np.clip(x, -1, 1) * 32767).astype(np.int16) if x.dtype != np.int16 else x,
                 feats=f, x_q=x_q, logits=acc, logp=lpq, decode=json.dumps(hyp), text=text,
                 **{f"out_{k.replace('.', '_')}": v for k, v in outs.items()})

    try:
        torch.onnx.export(m, torch.zeros(1, 150, NMEL), out / "model.onnx", input_names=["feats"], output_names=["logp"],
                          dynamic_axes={"feats": {1: "T"}, "logp": {1: "T2"}}, opset_version=13, dynamo=False)
    except Exception as e:   # the DIY path does not need it
        print("onnx export skipped:", e)
    shutil.copy(lx.LEXICON, out / "lexicon.yaml")
    if cal_f.exists():
        shutil.copy(cal_f, out / "reject_calib.json")
    n_w = sum(Q["wq"].size for Q in q["layers"])
    man = dict(commit=commit, run=a.run, format=FORMAT, weights=n_w, model_bin_bytes=meta["bytes"],
               int8_vs_float_greedy_agreement=round(agree / max(1, tot), 4), fold_max_err=fold_err,
               golden=len(golden), reject_calib=bool(cal_f.exists()),
               files={p.name: hashlib.sha256(p.read_bytes()).hexdigest()[:16] for p in sorted(out.iterdir()) if p.is_file()})
    (out / "MANIFEST.json").write_text(json.dumps(man, indent=1))
    print(json.dumps(man, indent=1))
    print("export:", out)


if __name__ == "__main__":
    main()
