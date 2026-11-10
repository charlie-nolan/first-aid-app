"""Research: how well could a small model map a FREE-FORM question to the right card?

Only ever picks a card (node key) or abstains; it never writes text. Two experiments:

  text   held-out sentences -> sentence embedding -> nearest registered phrase / card label.
         Upper bound for "voice understands the meaning, not just the sound".
  audio  test clips -> Whisper speech-to-text on the GPU -> the same ranker.
         Upper bound for the "free-form ears" idea (a co-processor), measured on noisy clips.

    python ranker_eval.py text  [--models all-MiniLM-L6-v2,Qwen/Qwen3-Embedding-0.6B]
    python ranker_eval.py audio --clips $WORK/clips [--whisper base.en] [--limit 1500]
Writes a Markdown report to --out (default $WORK/reports/ranker-<mode>.md).
"""
from __future__ import annotations

import argparse, json, sys
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
PLB = HERE.parent / "plb"
sys.path.insert(0, str(PLB))
from plb.voicetest import load_utterances  # noqa: E402

INTER = PLB / "build" / "intermediate"


def candidates() -> list[tuple[str, str]]:
    """(text, node key): every registered phrase plus every card's on-screen label."""
    trees = json.loads((INTER / "trees.json").read_text())
    reviewed = {t["id"] for t in trees["trees"] if not t.get("unreviewed")}
    nodes = {n["id"]: n for n in trees["nodes"]}
    out = [(c["spoken"], nodes[c["node"]]["key"]) for c in json.loads((INTER / "phrases.json").read_text())]
    out += [(n["label"], n["key"]) for n in trees["nodes"] if n["tree"] in reviewed and n.get("label")]
    return out


def score(model, cands, queries: list[dict], text_of) -> dict:
    ce = model.encode([c[0] for c in cands], normalize_embeddings=True, batch_size=64)
    qe = model.encode([text_of(q) or "" for q in queries], normalize_embeddings=True, batch_size=64)
    sims = qe @ ce.T
    res = []
    for q, s in zip(queries, sims):
        order = np.argsort(-s)
        seen, top = set(), []
        for j in order:
            k = cands[j][1]
            if k not in seen:
                seen.add(k); top.append((k, float(s[j])))
            if len(top) == 3:
                break
        res.append((q, top))
    rows = []
    for thr in (0.0, 0.4, 0.5, 0.6, 0.7):
        c = Counter()
        for q, top in res:
            best, sc = top[0]
            if sc < thr:
                c["ignored" if q["off_topic"] else "abstain"] += 1
            elif q["off_topic"]:
                c["FALSE"] += 1
            elif best in q["want"]:
                c["top1"] += 1
            elif any(k in q["want"] for k, _ in top):
                c["top3"] += 1
            elif best.split("/")[0] == q["want"][0].split("/")[0]:
                c["same topic"] += 1
            else:
                c["WRONG"] += 1
        rows.append((thr, c))
    return {"rows": rows, "res": res}


def report(title, n, results) -> str:
    cols = ["top1", "top3", "same topic", "abstain", "WRONG", "FALSE", "ignored"]
    md = [f"# {title}", "", f"{n} queries. top1 = right card first; top3 = right card in a 3-way picker; "
          "FALSE = an off-topic ask opened a card; ignored = off-topic ask correctly abstained.", ""]
    for name, r in results.items():
        md += [f"## {name}", "", "| min similarity | " + " | ".join(cols) + " |", "|---" * (len(cols) + 1) + "|"]
        for thr, c in r["rows"]:
            md.append(f"| {thr} | " + " | ".join(str(c[k]) for k in cols) + " |")
        misses = [(q, top) for q, top in r["res"] if not q["off_topic"] and top[0][0] not in q["want"]][:25]
        md += ["", "First misses:", "", "| said | heard as text | wanted | got (score) |", "|---|---|---|---|"]
        md += [f"| {q['text']} | {q.get('heard_text', '')} | {q['want'][0]} | {top[0][0]} ({top[0][1]:.2f}) |"
               for q, top in misses]
        md.append("")
    return "\n".join(md)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["text", "audio"])
    ap.add_argument("--models", default="sentence-transformers/all-MiniLM-L6-v2,Qwen/Qwen3-Embedding-0.6B")
    ap.add_argument("--clips", type=Path)
    ap.add_argument("--whisper", default="base.en")
    ap.add_argument("--limit", type=int, default=1500)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    from sentence_transformers import SentenceTransformer
    cands = candidates()

    if a.mode == "text":
        queries = load_utterances(PLB / "content" / "phrases" / "synthetic")
        text_of = lambda q: q["text"]
    else:
        from faster_whisper import WhisperModel
        rows = [json.loads(l) for l in (a.clips / "manifest.jsonl").read_text().splitlines()]
        rows = [r for r in rows if r["source"] != "registered"][: a.limit]
        wm = WhisperModel(a.whisper, device="cuda", compute_type="float16")
        for i, r in enumerate(rows):
            segs, _ = wm.transcribe(str(a.clips / r["wav"]), language="en", beam_size=1, vad_filter=True)
            r["heard_text"] = " ".join(s.text for s in segs).strip().lower()
            if i % 100 == 0:
                print(f"whisper {i}/{len(rows)}: {r['text']!r} -> {r['heard_text']!r}", flush=True)
        queries = rows
        text_of = lambda q: q["heard_text"]

    results = {}
    for name in a.models.split(","):
        print("model", name, flush=True)
        m = SentenceTransformer(name, device="cuda")
        results[name] = score(m, cands, queries, text_of)
        del m
    title = "Card ranker: text" if a.mode == "text" else f"Card ranker: Whisper {a.whisper} on clips"
    md = report(title, len(queries), results)
    out = a.out or Path(__import__("os").environ.get("WORK", ".")) / "reports" / f"ranker-{a.mode}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    print(md); print("->", out)


if __name__ == "__main__":
    main()
