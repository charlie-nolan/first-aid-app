"""Train the acoustic model with CTC. Pause-safe and resumable.

    python -m fasr train --run pretrain --train $WORK/speech/librispeech/train.jsonl \
        --dev $WORK/speech/librispeech/dev.jsonl --size s --hours 10
    python -m fasr train --run firstaid --init pretrain --train $WORK/speech/tts/train.jsonl \
        --train $WORK/speech/me/train.jsonl:4 --dev $WORK/speech/tts/dev.jsonl --hours 4

Runs live in $WORK/speech/runs/<run>/ (ckpt.pt = latest, best.pt = best dev phone error).
Running the same command again resumes from ckpt.pt.

Sharing the GPU with the cyberdeck:
  * a checkpoint is written every --ckpt-min minutes and on exit;
  * if the pause file exists ($FASR_PAUSE_FILE, default $WORK/PAUSE_GPU), or the process gets
    SIGUSR1, training saves, frees the GPU and waits; it resumes when the file is gone or on
    SIGUSR2 (SIGCONT also works after a SIGSTOP);
  * SIGTERM / Ctrl-C saves and exits; the next run resumes.
"""
from __future__ import annotations

import argparse
import math
import os
import random
import signal
import time
from pathlib import Path

import numpy as np
import torch

from .data import Augment, G2P, SpeechSet, collate, read_manifest, spec_augment, weighted_sampler
from .features import logmel_torch
from .model import AcousticModel
from .phones import BLANK

WORK = Path(os.environ.get("WORK", "/mnt/data/first-aid-gpu"))


class Pauser:
    def __init__(self, pause_file: Path):
        self.file, self.flag, self.stop = pause_file, False, False
        signal.signal(signal.SIGUSR1, lambda *_: setattr(self, "flag", True))
        signal.signal(signal.SIGUSR2, lambda *_: setattr(self, "flag", False))
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "stop", True))

    def wanted(self) -> bool:
        return self.flag or self.file.exists()


def greedy(logp: torch.Tensor, n: int) -> list[int]:
    ids = logp[:n].argmax(-1).tolist()
    out, prev = [], None
    for i in ids:
        if i != prev and i != BLANK:
            out.append(i)
        prev = i
    return out


def edit_distance(a: list, b: list) -> int:
    d = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, d[0] = d[0], i
        for j, y in enumerate(b, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (x != y))
    return d[-1]


@torch.no_grad()
def evaluate(model, items, dev: torch.device, bs: int = 32, limit: int = 2000) -> float:
    """Phone error rate (greedy) on clean dev audio."""
    model.eval()
    items = items[:limit]
    errs = toks = 0
    dl = torch.utils.data.DataLoader(SpeechSet(items, None), batch_size=bs, collate_fn=collate, num_workers=2)
    for X, xl, Y, yl, ids in dl:
        f = logmel_torch(X.to(dev))
        lp = model(f).float().cpu()
        fl = model.out_len(1 + (xl - 400) // 160)
        for k, i in enumerate(ids):
            hyp = greedy(lp[k], int(fl[k]))
            ref = items[i]["y"]
            errs += edit_distance(hyp, ref)
            toks += len(ref)
    model.train()
    return errs / max(1, toks)


def move_opt(opt, dev):
    for st in opt.state.values():
        for k, v in st.items():
            if torch.is_tensor(v) and k != "step":
                st[k] = v.to(dev)


def feature_stats(items, n: int = 300) -> tuple[torch.Tensor, torch.Tensor]:
    from .data import load_wav
    from .features import logmel
    fs = [logmel(load_wav(it["wav"])) for it in random.sample(items, min(n, len(items)))]
    a = np.concatenate(fs)
    return torch.from_numpy(a.mean(0)), torch.from_numpy(a.std(0) + 1e-3)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fasr train")
    ap.add_argument("--run", required=True)
    ap.add_argument("--train", action="append", required=True,
                    help="manifest[:weight]; repeat to mix sets (weight = how often its clips are drawn)")
    ap.add_argument("--dev", required=True)
    ap.add_argument("--init", help="start from another run's best.pt (fine-tuning)")
    ap.add_argument("--size", default="s")
    ap.add_argument("--hours", type=float, default=4.0, help="training budget (wall clock, excluding pauses)")
    ap.add_argument("--bs", type=int, default=48)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--noise-dir", type=Path)
    ap.add_argument("--ckpt-min", type=float, default=10)
    ap.add_argument("--eval-min", type=float, default=30)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--cpu", action="store_true")
    a = ap.parse_args(argv)

    run = WORK / "speech" / "runs" / a.run
    run.mkdir(parents=True, exist_ok=True)
    log = (run / "log.txt").open("a")

    def say(*s):
        msg = time.strftime("%m-%d %H:%M:%S ") + " ".join(str(x) for x in s)
        print(msg, flush=True)
        log.write(msg + "\n"); log.flush()

    dev = torch.device("cpu" if a.cpu or not torch.cuda.is_available() else "cuda")
    g2p = G2P()
    items = []
    for spec in a.train:
        path, _, w = spec.partition(":")
        its = read_manifest(Path(path), g2p)
        tot = len(its)
        for it in its:
            it["w"] *= float(w or 1) / max(1, tot) * 1e4   # each set's share follows its weight
        items += its
    dev_items = read_manifest(Path(a.dev), g2p)
    if not items:
        raise SystemExit("no training utterances")

    model = AcousticModel(a.size).to(dev)
    ck = run / "ckpt.pt"
    state = {"step": 0, "secs": 0.0, "best": 9.9}
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-2)
    if ck.exists():
        d = torch.load(ck, map_location=dev)
        model.load_state_dict(d["model"]); opt.load_state_dict(d["opt"]); state = d["state"]
        say(f"resumed {a.run} at step {state['step']}, {state['secs']/3600:.2f} h done, best PER {state['best']:.3f}")
    else:
        if a.init:
            d = torch.load(WORK / "speech" / "runs" / a.init / "best.pt", map_location=dev)
            model.load_state_dict(d["model"])
            say(f"initialised from {a.init}")
        else:
            mu, sd = feature_stats(items)
            model.mu.copy_(mu); model.sd.copy_(sd)
        say(f"new run {a.run}: {model.n_params()/1e6:.2f} M weights, {len(items)} train, {len(dev_items)} dev, {dev}")

    budget = a.hours * 3600
    def lr_at(s):  # warmup 3% then cosine, on wall-clock fraction
        f = s / budget
        return a.lr * (f / 0.03 if f < 0.03 else 0.5 * (1 + math.cos(math.pi * min(1.0, (f - 0.03) / 0.97))))

    def save(path=ck):
        tmp = path.with_suffix(".tmp")
        torch.save({"model": model.state_dict(), "opt": opt.state_dict(), "state": state, "size": a.size}, tmp)
        os.replace(tmp, path)

    pauser = Pauser(Path(os.environ.get("FASR_PAUSE_FILE", WORK / "PAUSE_GPU")))
    ctc = torch.nn.CTCLoss(blank=BLANK, zero_infinity=True, reduction="none")
    scaler = torch.amp.GradScaler(enabled=dev.type == "cuda")
    rng = random.Random(state["step"])
    aug = Augment(a.noise_dir, seed=state["step"])
    last_ck = last_ev = time.time()
    t_mark = time.time()
    model.train()
    try:
        def used():
            return state["secs"] + time.time() - t_mark

        while used() < budget and not pauser.stop:
            dl = torch.utils.data.DataLoader(SpeechSet(items, aug), batch_size=a.bs, collate_fn=collate,
                                             sampler=weighted_sampler(items, 20000), num_workers=a.workers,
                                             persistent_workers=False)
            for X, xl, Y, yl, _ in dl:
                if pauser.stop or used() >= budget:
                    break
                if pauser.wanted():
                    state["secs"] += time.time() - t_mark
                    save(); say("paused (GPU wanted elsewhere): checkpoint saved, GPU memory freed")
                    del X, Y
                    model.cpu(); move_opt(opt, "cpu"); torch.cuda.empty_cache()
                    while pauser.wanted() and not pauser.stop:
                        time.sleep(15)
                    model.to(dev); move_opt(opt, dev)
                    say("resumed after pause"); t_mark = time.time()
                    break
                with torch.no_grad():
                    f = logmel_torch(X.to(dev, non_blocking=True))
                    f = torch.stack([spec_augment(f[i], rng) for i in range(len(f))])
                fl = model.out_len(1 + (xl - 400) // 160).to(dev)
                for g in opt.param_groups:
                    g["lr"] = lr_at(state["secs"] + time.time() - t_mark)
                with torch.autocast(dev.type, enabled=dev.type == "cuda"):
                    lp = model(f)
                loss = ctc(lp.float().transpose(0, 1), Y.to(dev), fl, yl.to(dev))
                loss = (loss / yl.to(dev).clamp(min=1)).mean()
                opt.zero_grad(set_to_none=True)
                scaler.scale(loss).backward()
                scaler.unscale_(opt)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
                scaler.step(opt); scaler.update()
                state["step"] += 1
                now = time.time()
                if state["step"] % 100 == 0:
                    say(f"step {state['step']} loss {loss.item():.3f} lr {opt.param_groups[0]['lr']:.2e} "
                        f"{(state['secs'] + now - t_mark)/3600:.2f}/{a.hours} h")
                if now - last_ev > a.eval_min * 60:
                    per = evaluate(model, dev_items, dev)
                    say(f"dev phone error {per:.3f} (best {state['best']:.3f})")
                    if per < state["best"]:
                        state["best"] = per; save(run / "best.pt")
                    last_ev = time.time()
                if now - last_ck > a.ckpt_min * 60:
                    state["secs"] += now - t_mark; t_mark = now
                    save(); last_ck = now
    except KeyboardInterrupt:
        pauser.stop = True
    state["secs"] += time.time() - t_mark
    per = evaluate(model, dev_items, dev)
    if per < state["best"]:
        state["best"] = per; save(run / "best.pt")
    save()
    say(f"{'stopped' if pauser.stop else 'finished'}: dev phone error {per:.3f}, best {state['best']:.3f}")
