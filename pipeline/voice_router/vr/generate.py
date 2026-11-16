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
        "options": {"temperature": 0.9, "num_ctx": 4096},
        "messages": [{"role": "system", "content": STYLE},
                     {"role": "user", "content": f"{prompt}\nWrite {n} different ones."}]})
    r.raise_for_status()
    return json.loads(r.json()["message"]["content"]).get("utterances", [])


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


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--trees", type=Path, required=True)
    ap.add_argument("--model", default="qwen3:30b-a3b-instruct-2507-q4_K_M")
    ap.add_argument("--ollama", default="http://127.0.0.1:11434")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--out", type=Path, default=HERE / "out" / "generated")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    topics = yaml.safe_load((CONTENT / "voice" / "topics.yaml").read_text())
    held = [t for v in topics["topics"].values() for t in v["test"]] + topics["reject"]["test"]
    seen: set[str] = set()
    res = {}
    for name, t in topics["topics"].items():
        got = ask(a.model, a.ollama, f"Topic: {name.replace('_', ' ')} (first-aid / survival tree "
                  f"'{t['open']}'). Examples: " + "; ".join(t["train"]), a.n)
        res[name] = clean(got, held, seen)
        print(f"{name}: {len(res[name])}", flush=True)
    (a.out / "router.yaml").write_text(yaml.safe_dump({"topics": res}, sort_keys=False))
    for ef in sorted((CONTENT / "voice" / "experts").glob("*.yaml")):
        e = yaml.safe_load(ef.read_text())
        tree = yaml.safe_load((a.trees / f"{e['tree']}.yaml").read_text())
        eheld = held + [u for s in e["nodes"].values() for u in s.get("test", [])]
        nodes = {}
        for key, s in e["nodes"].items():
            card = tree["nodes"][key]["label"]
            got = ask(a.model, a.ollama, f"The person is inside the '{tree['title']}' guide. They want the "
                      f"card titled \"{card}\". Things they'd say to get there. Examples: "
                      + "; ".join(s.get("train", [])), max(10, a.n // 3))
            nodes[key] = clean(got, eheld, seen)
        (a.out / f"expert-{e['tree']}.yaml").write_text(yaml.safe_dump({"nodes": nodes}, sort_keys=False))
        print(f"expert {e['tree']}: {sum(map(len, nodes.values()))}", flush=True)


if __name__ == "__main__":
    main()
