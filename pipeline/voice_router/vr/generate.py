"""Desktop only: widen the seed sentences with a LOCAL model (Ollama), for training.

For every router topic and every expert node, asks for N more ways a stressed hiker
might say it. Held-out test sentences (and near copies of them) are filtered out, so the
test score stays honest. Output: out/generated/router.yaml and expert-<tree>.yaml, which
`python -m vr ... --extra out/generated` adds to training. Nothing generated ships: it
only teaches the router and experts which card to open.

    python -m vr.generate --trees DIR [--model qwen3:30b-a3b-instruct-2507-q4_K_M] [--n 40]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import requests
import yaml

from .lexicon import tokenize
from .data import load_topics, load_tree
from .train import CONTENT, HERE

SCHEMA = {"type": "object", "properties": {"utterances": {"type": "array", "items": {"type": "string"}}},
          "required": ["utterances"]}
STYLE = ("Write short things a person might SAY OUT LOUD to a pocket first-aid device while hiking or "
         "camping with no phone signal. Everyday civilian speech: stressed, casual, first person and "
         "about someone else, adults and kids, 2 to 10 words, no punctuation, lower case. No violence "
         "or military wording. Do not repeat the examples. Return JSON {\"utterances\": [...]}.")


def ask(model, url, prompt, n):
    r = requests.post(f"{url}/api/chat", timeout=900, json={
        "model": model, "stream": False, "format": SCHEMA,
        "options": {"temperature": 0.9, "num_ctx": 4096, "num_predict": 1500, "repeat_penalty": 1.1},
        "messages": [{"role": "system", "content": STYLE},
                     {"role": "user", "content": f"{prompt}\nWrite {n} different ones."}]})
    r.raise_for_status()
    txt = r.json()["message"]["content"]
    try:
        return json.loads(txt).get("utterances", [])
    except json.JSONDecodeError:                    # cut off by num_predict: keep the whole strings
        import re
        return re.findall(r'"([^"\\]{3,120})"', txt.split("[", 1)[-1])


def clean(cands, held_out, seen):
    held = [set(tokenize(t)) for t in held_out]
    out = []
    for c in cands:
        w = tokenize(c)
        if not 1 <= len(w) <= 14:
            continue
        s = " ".join(w)
        sw = set(w)
        if s in seen or any(len(sw & h) / len(sw | h) > 0.7 for h in held):
            continue
        seen.add(s); out.append(s)
    return out


class TeacherFilter:
    """Drops a generated line when a sentence-embedding model thinks it sits nearer another
    class's seed sentences than its own (Qwen mislabels about 1 in 5)."""

    def __init__(self, name, groups: dict[str, list[str]]):
        from sentence_transformers import SentenceTransformer
        import numpy as np
        self.np = np
        self.m = SentenceTransformer(name)
        self.names = list(groups)
        self.cent = np.stack([self.m.encode(g, normalize_embeddings=True).mean(0) for g in groups.values()])
        self.cent /= np.linalg.norm(self.cent, axis=1, keepdims=True)

    def keep(self, label: str, lines: list[str], top: int = 2) -> list[str]:
        if not lines or label not in self.names:
            return lines
        e = self.m.encode(lines, normalize_embeddings=True)
        order = self.np.argsort(-(e @ self.cent.T), axis=1)[:, :top]
        i = self.names.index(label)
        return [l for l, o in zip(lines, order) if i in o]


def pair_prompts(topics):
    """(topic, prompt) for each side of each confusable pair: lines that are clearly X and not Y."""
    out = []
    for x, y in topics.get("confusable", []):
        tx, ty = topics["topics"][x], topics["topics"][y]
        for me, other, t in ((x, y, tx), (y, x, ty)):
            out.append((me, f"Topic: {me.replace('_', ' ')}. Write lines that are clearly about {me.replace('_', ' ')} "
                            f"and could NOT be mistaken for {other.replace('_', ' ')}. Include the telling detail "
                            f"that separates them. Examples of {me}: " + "; ".join(t["train"][:6])))
    return out


def write_pairs(a, topics, held, seen):
    res: dict[str, list[str]] = {}
    for name, prompt in pair_prompts(topics):
        res.setdefault(name, []).extend(clean(ask(a.model, a.ollama, prompt, max(10, a.n // 2)), held, seen))
        (a.out / "router.yaml").write_text(yaml.safe_dump({"topics": res}, sort_keys=False))
        print(f"{name}: {len(res[name])}", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--trees", type=Path, nargs="+", required=True)
    ap.add_argument("--model", default="qwen3:30b-a3b-instruct-2507-q4_K_M")
    ap.add_argument("--ollama", default="http://127.0.0.1:11434")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--out", type=Path, default=HERE / "out" / "generated")
    ap.add_argument("--pairs", action="store_true", help="only write contrast lines for the `confusable` "
                    "topic pairs in topics.yaml (into --out/router.yaml)")
    ap.add_argument("--filter", help="sentence-transformers model that drops mislabelled lines, "
                    "e.g. sentence-transformers/all-MiniLM-L6-v2")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    topics = load_topics(CONTENT / "voice" / "topics.yaml")
    held = [t for v in topics["topics"].values() for t in v["test"]] + topics["reject"]["test"]
    seen: set[str] = set()
    if a.pairs:
        return write_pairs(a, topics, held, seen)
    groups = {n: t["train"] for n, t in topics["topics"].items()} | {"reject": topics["reject"]["train"]}
    filt = TeacherFilter(a.filter, groups) if a.filter else None
    out_file = a.out / "router.yaml"
    res = (yaml.safe_load(out_file.read_text()) or {}).get("topics", {}) if out_file.exists() else {}
    for name, t in list(topics["topics"].items()) + [("reject", None)]:
        if name in res:                                   # resume after a crash
            continue
        if t is None:
            prompt = ("Things a hiker might say to a pocket device that are NOT a first-aid or survival "
                      "question: small talk, trail chat, weather small talk, device questions. Examples: "
                      + "; ".join(topics["reject"]["train"]))
        else:
            prompt = (f"Topic: {name.replace('_', ' ')} (first-aid / survival tree '{t['open']}'). "
                      "Every line must clearly be about this topic. Examples: " + "; ".join(t["train"]))
        got = clean(ask(a.model, a.ollama, prompt, a.n), held, seen)
        before = len(got)
        if filt:
            got = filt.keep(name, got)
        res[name] = got
        out_file.write_text(yaml.safe_dump({"topics": res}, sort_keys=False))
        print(f"{name}: {len(got)} kept of {before}", flush=True)
    efiles = sorted((CONTENT / "voice" / "experts").glob("*.yaml")) + \
        sorted((CONTENT / "voice" / "experts_personal").glob("*.yaml"))
    for ef in efiles:
        e = yaml.safe_load(ef.read_text())
        tree = load_tree(a.trees, e["tree"])
        if tree is None:
            print(f"skip {ef.name}: tree {e['tree']} not in --trees", flush=True)
            continue
        eheld = held + [u for s in e["nodes"].values() for u in s.get("test", [])]
        nodes = {}
        nfilt = TeacherFilter(a.filter, {k: v.get("train", []) for k, v in e["nodes"].items() if v.get("train")}) \
            if a.filter else None
        for key, s in e["nodes"].items():
            card = tree["nodes"][key]["label"]
            got = ask(a.model, a.ollama, f"The person is inside the '{tree['title']}' guide. They want the "
                      f"card titled \"{card}\". Things they'd say to get there. Examples: "
                      + "; ".join(s.get("train", [])), max(10, a.n // 3))
            nodes[key] = clean(got, eheld, seen)
            if nfilt:
                nodes[key] = nfilt.keep(key, nodes[key], top=3)
            (a.out / f"expert-{e['tree']}.yaml").write_text(yaml.safe_dump({"nodes": nodes}, sort_keys=False))
        print(f"expert {e['tree']}: {sum(map(len, nodes.values()))}", flush=True)


if __name__ == "__main__":
    main()
