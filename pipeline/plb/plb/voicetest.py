"""Voice stress test: many ways a person might say it, against the fixed phrase list.

expect.yaml checks that each *registered* phrase opens the right node. People in a hurry
don't say the registered phrase, though. This scores a held-out set of synthetic
utterances (content/phrases/synthetic/*.yaml, written by an LLM and read by a person)
against the phrase list the device will actually load, and reports:

  hit       lands on the node the utterance asks for
  near      lands on the topic's first screen or a step on the way to the wanted node
            (one or two taps away, nothing wrong on screen)
  OFF       lands on a different step of the right topic (wrong card for this ask)
  miss      nothing close enough: the device says it didn't catch that (safe, annoying)
  WRONG     lands in a different topic (unsafe: the wrong card on screen)
  FALSE     an off-topic utterance (a topic with no tree yet) opens a card

ESP-SR MultiNet can't be run here, so a stand-in decides what it would hear: the
registered phrase that *sounds* most like the utterance (a rough phonetic key per word,
then word- and letter-level similarity), accepted above a threshold. It is a proxy for
the real recogniser, good for finding phrases that are missing or too alike; bench
numbers from the device are what count. The threshold is swept in the report.

Two listening contexts, as the firmware loads them (cart_voice_list):
  home      always + every topic's entry phrases (after wake, at the Library)
  in-topic  home + the current topic's own node phrases
"""
from __future__ import annotations

import difflib
import json
import re
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

import yaml

from .phrases import normalize

THRESHOLD = 0.72
SWEEP = (0.6, 0.65, 0.7, 0.72, 0.75, 0.8, 0.85, 0.9)
NONE = "none"

# filler a stressed speaker adds that carries no topic
_FILLER = {"um", "uh", "like", "please", "okay", "ok", "so", "hey", "oh", "god", "just",
           "really", "very", "quick", "quickly", "help", "me", "i", "need", "to", "what",
           "do", "the", "a", "an", "is", "my", "it", "its", "and", "we", "you", "of", "on",
           "in", "for", "how", "should", "can", "there", "this", "that", "with", "im", "got"}


@lru_cache(maxsize=None)
def sound_key(word: str) -> str:
    """A rough phonetic key: enough to treat 'tourniquet'/'turnicat' or 'bleedin'/'bleeding'
    as close, not a real G2P."""
    w = word.lower()
    for a, b in (("ph", "f"), ("ck", "k"), ("qu", "k"), ("gh", ""), ("kn", "n"), ("wr", "r"),
                 ("th", "t"), ("sh", "s"), ("ch", "k"), ("ce", "se"), ("ci", "si"), ("x", "ks"),
                 ("c", "k"), ("q", "k"), ("z", "s"), ("v", "f"), ("ing", "in"), ("y", "i")):
        w = w.replace(a, b)
    head, tail = w[:1], re.sub(r"[aeiou]+", "", w[1:])
    return re.sub(r"(.)\1+", r"\1", head + tail)


def similarity(utt: str, phrase: str) -> float:
    a, b = normalize(utt).split(), normalize(phrase).split()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ka, kb = [sound_key(w) for w in a], [sound_key(w) for w in b]
    # word level: sequence of sound keys
    wl = difflib.SequenceMatcher(None, ka, kb, autojunk=False).ratio()
    # content words of the phrase that the utterance contains (by sound)
    content = [k for w, k in zip(b, kb) if w not in _FILLER] or kb
    have = set(ka)
    cov = sum(k in have for k in content) / len(content)
    # letter level on the sound string
    ll = difflib.SequenceMatcher(None, " ".join(ka), " ".join(kb), autojunk=False).ratio()
    return round(0.35 * wl + 0.35 * ll + 0.30 * cov, 4)


# ---------------------------------------------------------------- data

def load_commands(inter: Path) -> tuple[list[dict], dict]:
    trees = json.loads((inter / "trees.json").read_text())
    cmds = json.loads((inter / "phrases.json").read_text())
    nodes = {n["id"]: n for n in trees["nodes"]}
    roots = {t["root"] for t in trees["trees"] if "root" in t}
    for c in cmds:
        n = nodes[c["node"]]
        c["key"], c["tree"] = n["key"], n["tree"]
        c["entry"] = c["node"] in roots or n["key"].endswith("/" + _root_key(trees, n["tree"]))
    return cmds, trees


def _root_key(trees, tid):
    for t in trees["trees"]:
        if t["id"] == tid:
            r = t.get("root")
            if isinstance(r, str):
                return r
            for n in trees["nodes"]:
                if n["id"] == r:
                    return n["key"].split("/", 1)[1]
    return ""


def load_utterances(path: Path) -> list[dict]:
    """Each file: {expect: {node_key: [utterance, ...]}, accept: {utterance: [keys]},
    off_topic: {topic: [utterance, ...]}}. Entries under `expect` may list extra
    acceptable nodes as `utterance | key2 | key3`."""
    out = []
    files = sorted(path.glob("*.yaml")) if path.is_dir() else [path]
    for f in files:
        d = yaml.safe_load(f.read_text()) or {}
        for key, utts in (d.get("expect") or {}).items():
            for u in utts or []:
                parts = [p.strip() for p in str(u).split("|")]
                out.append({"text": parts[0], "want": [key] + parts[1:], "topic": key.split("/")[0],
                            "file": f.name, "off_topic": False})
        for topic, utts in (d.get("off_topic") or {}).items():
            for u in utts or []:
                parts = [p.strip() for p in str(u).split("|")]
                out.append({"text": parts[0], "want": [NONE] + parts[1:], "topic": topic,
                            "file": f.name, "off_topic": True})
    return out


# ---------------------------------------------------------------- scoring

def best_matches(utt: str, cands: list[dict], k: int = 3) -> list[tuple[float, dict]]:
    scored = sorted(((similarity(utt, c["phrase"]), c) for c in cands),
                    key=lambda x: -x[0])
    return scored[:k]


def classify(u: dict, top: list[tuple[float, dict]], thr: float, tree_ids: set[str],
             up: dict | None = None) -> str:
    if not top or top[0][0] < thr:
        if u["off_topic"] or NONE in u["want"]:
            return "hit" if u["off_topic"] or u["want"] == [NONE] else "miss"
        return "miss"
    got = top[0][1]
    if got["key"] in u["want"]:
        return "hit"
    if u["off_topic"]:
        # a topic with a tree of its own now: landing in it is right
        if u["topic"] in tree_ids:
            return "hit" if got["tree"] == u["topic"] else "WRONG"
        return "FALSE"
    want_trees = {w.split("/")[0] for w in u["want"] if w != NONE}
    if got["tree"] not in want_trees:
        return "WRONG"
    on_path = set()
    for w in u["want"]:
        on_path |= (up or {}).get(w, set())
    return "near" if got["entry"] or got["key"] in on_path else "OFF"


def ancestors(trees: dict) -> dict[str, set[str]]:
    """node key -> keys of every node with a path to it."""
    key = {n["id"]: n["key"] for n in trees["nodes"]}
    parents = defaultdict(set)
    for n in trees["nodes"]:
        for ch in n["choices"]:
            parents[key[ch["to"]]].add(n["key"])
    out = {}
    for k in key.values():
        seen, todo = set(), list(parents[k])
        while todo:
            p = todo.pop()
            if p not in seen:
                seen.add(p)
                todo += parents[p]
        out[k] = seen
    return out


def run(root: Path, inter: Path, utt_path: Path, thr: float = THRESHOLD) -> dict:
    cmds, trees = load_commands(inter)
    tree_ids = {t["id"] for t in trees["trees"]}
    up = ancestors(trees)
    utts = load_utterances(utt_path)
    registered = {c["phrase"] for c in cmds}
    home = [c for c in cmds if c["always"] or c["entry"]]
    by_tree = defaultdict(list)
    for c in cmds:
        by_tree[c["tree"]].append(c)

    rows = []
    for u in utts:
        ctxs = {"home": home}
        t = u["topic"]
        if t in tree_ids:
            ctxs["in-topic"] = home + [c for c in by_tree[t] if c not in home]
        for ctx, cands in ctxs.items():
            top = best_matches(u["text"], cands)
            rows.append({**u, "ctx": ctx, "registered": normalize(u["text"]) in registered,
                         "top": [(s, c["key"], c["phrase"]) for s, c in top],
                         "result": classify(u, top, thr, tree_ids, up),
                         "sweep": {str(x): classify(u, top, x, tree_ids, up) for x in SWEEP}})

    def tally(rs, field="result"):
        return Counter(r[field] if field == "result" else r["sweep"][field] for r in rs)

    in_scope = [r for r in rows if not r["off_topic"] and r["ctx"] == "in-topic"]
    hits = sum(r["result"] == "hit" for r in in_scope)
    rep = {
        "threshold": thr, "rows": rows, "n_utterances": len(utts),
        "n_phrases": len(cmds), "home_phrases": len(home),
        "by_ctx": {ctx: dict(tally([r for r in rows if r["ctx"] == ctx and not r["off_topic"]]))
                   for ctx in ("home", "in-topic")},
        "off_topic": dict(tally([r for r in rows if r["off_topic"] and r["ctx"] == "home"])),
        "sweep": {str(x): {
            "in-topic": dict(tally([r for r in rows if r["ctx"] == "in-topic"
                                    and not r["off_topic"]], str(x))),
            "off_topic": dict(tally([r for r in rows if r["off_topic"] and r["ctx"] == "home"],
                                    str(x)))} for x in SWEEP},
        "in_scope_accuracy": hits / len(in_scope) if in_scope else 0.0,
        "per_node": {},
        "attractors": Counter(),
        "collisions": phrase_collisions(cmds),
    }
    per = defaultdict(Counter)
    for r in in_scope:
        per[r["want"][0]][r["result"]] += 1
    rep["per_node"] = {k: dict(v) for k, v in sorted(per.items())}
    for r in rows:
        if r["result"] in ("WRONG", "FALSE", "OFF"):
            rep["attractors"][(r["top"][0][2], r["top"][0][1])] += 1
    rep["unsafe"] = [r for r in rows if r["result"] in ("WRONG", "FALSE")]
    rep["off_step"] = [r for r in rows if r["result"] == "OFF"]
    return rep


def phrase_collisions(cmds: list[dict], thr: float = 0.8) -> list[tuple]:
    """Registered phrases that sound alike, open different nodes, and can be in the
    same voice list at once."""
    out = []
    for i, a in enumerate(cmds):
        for b in cmds[i + 1:]:
            together = a["tree"] == b["tree"] or a["always"] or b["always"] or a["entry"] or b["entry"]
            if not together or a["node"] == b["node"]:
                continue
            s = max(similarity(a["phrase"], b["phrase"]), similarity(b["phrase"], a["phrase"]))
            if s >= thr:
                out.append((round(s, 3), a["phrase"], a["key"], b["phrase"], b["key"],
                            "different topics" if a["tree"] != b["tree"] else "same topic"))
    return sorted(out, reverse=True)


# ---------------------------------------------------------------- report

def summary(rep: dict) -> str:
    it = rep["by_ctx"].get("in-topic", {})
    n = sum(it.values()) or 1
    off = rep["off_topic"]
    return (f"{rep['n_utterances']} utterances vs {rep['n_phrases']} phrases: in-topic "
            f"{it.get('hit', 0) * 100 // n}% hit, {it.get('near', 0)} near, {it.get('miss', 0)} miss, "
            f"{it.get('OFF', 0)} wrong step, "
            f"{it.get('WRONG', 0)} wrong topic; off-topic {off.get('FALSE', 0)} false triggers "
            f"of {sum(off.values())}")


def report_md(rep: dict) -> str:
    L = ["# Voice stress test", "",
         f"{rep['n_utterances']} synthetic utterances against {rep['n_phrases']} registered phrases "
         f"({rep['home_phrases']} in the home list). Recogniser stand-in, threshold "
         f"{rep['threshold']}. These are proxy numbers for finding gaps; the bench is the real test.",
         "", "## Results", "", "| Context | hit | near | miss | OFF step | WRONG topic |",
         "|---|---|---|---|---|---|"]
    for ctx in ("home", "in-topic"):
        c = rep["by_ctx"].get(ctx, {})
        L.append(f"| {ctx} | {c.get('hit', 0)} | {c.get('near', 0)} | {c.get('miss', 0)} | "
                 f"{c.get('OFF', 0)} | {c.get('WRONG', 0)} |")
    off = rep["off_topic"]
    L += ["", f"Off-topic (topics with no tree yet, heard at home): {off.get('hit', 0)} correctly "
              f"ignored, **{off.get('FALSE', 0)} opened a card** they shouldn't.", "",
          "`near` = the topic's first screen or a step on the way. `OFF step` = the right topic "
          "but a card for a different situation. At the home screen only topic entry phrases are "
          "loaded, so a specific ask usually lands on the topic's first screen: that is expected.",
          "", "## Threshold sweep (in-topic, and off-topic false triggers)", "",
          "| threshold | hit | near | miss | OFF step | WRONG | off-topic FALSE |",
          "|---|---|---|---|---|---|---|"]
    for x, d in rep["sweep"].items():
        a, o = d["in-topic"], d["off_topic"]
        L.append(f"| {x} | {a.get('hit', 0)} | {a.get('near', 0)} | {a.get('miss', 0)} | "
                 f"{a.get('OFF', 0)} | {a.get('WRONG', 0)} | {o.get('FALSE', 0)} |")
    L += ["", "## Per node (in-topic)", "", "| node | hit | near | miss | OFF step |", "|---|---|---|---|---|"]
    for k, c in rep["per_node"].items():
        L.append(f"| {k} | {c.get('hit', 0)} | {c.get('near', 0)} | {c.get('miss', 0)} | "
                 f"{c.get('OFF', 0)} |")
    if rep["off_step"]:
        L += ["", "## Landed on the wrong step", "",
              "| context | said | wanted | heard as | score |", "|---|---|---|---|---|"]
        for r in rep["off_step"]:
            s, key, ph = r["top"][0]
            L.append(f"| {r['ctx']} | {r['text']} | {', '.join(r['want'])} | \"{ph}\" -> {key} | {s:.2f} |")
    if rep["unsafe"]:
        L += ["", "## Wrong topic or false trigger", "",
              "| context | said | wanted | heard as | score |", "|---|---|---|---|---|"]
        seen = set()
        for r in rep["unsafe"]:
            k = (r["text"], r["ctx"])
            if k in seen:
                continue
            seen.add(k)
            s, key, ph = r["top"][0]
            want = r["topic"] + " (no tree yet)" if r["off_topic"] else ", ".join(r["want"])
            L.append(f"| {r['ctx']} | {r['text']} | {want} | \"{ph}\" -> {key} | {s:.2f} |")
    if rep["attractors"]:
        L += ["", "## Phrases that pull in the wrong asks", ""]
        for (ph, key), n in rep["attractors"].most_common(15):
            L.append(f"- \"{ph}\" ({key}): {n}")
    misses = [r for r in rep["rows"] if r["ctx"] == "in-topic" and r["result"] in ("miss", "near")]
    if misses:
        L += ["", "## Missed or landed one step off (in-topic)", "",
              "Candidates for new phrases on the wanted node, after a read for tone.", "",
              "| said | wanted | best guess | score |", "|---|---|---|---|"]
        for r in misses:
            s, key, ph = r["top"][0]
            L.append(f"| {r['text']} | {r['want'][0]} | \"{ph}\" -> {key} | {s:.2f} |")
    L += ["", "## Registered phrases that sound alike (different nodes, same list)", ""]
    if rep["collisions"]:
        L += ["| score | phrase | node | phrase | node | |", "|---|---|---|---|---|---|"]
        for s, pa, ka, pb, kb, how in rep["collisions"]:
            L.append(f"| {s} | {pa} | {ka} | {pb} | {kb} | {how} |")
    else:
        L.append("None above 0.8.")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- optional generator

GEN_PROMPT = """You are writing test data for an offline wilderness first-aid device with a
fixed-phrase voice recogniser. For each screen listed below, write {n} different things a
real person might SAY to reach it: a hiker, parent, or friend, often scared or out of
breath, first or third person, short and long, plain everyday words, a few likely
speech-to-text slips. Civilian and backcountry situations only (knives, axes, rocks,
falls, glass, dogs, bikes, cooking). No violent, military or weapon wording. Do not repeat
the phrases the device already knows (listed per screen). Lower case, no punctuation
except apostrophes.

Also write {n_off} utterances, under "off_topic", that sound related but belong to other
first-aid topics (for example cpr, choking, hypothermia, heat, allergy, snakebite,
fracture, burns), keyed by that topic.

SCREENS:
{screens}
"""


def generate(inter: Path, tree: str, out: Path, n: int = 15, n_off: int = 40,
             model: str = "claude-opus-5-5") -> Path:
    """Ask Claude for new held-out utterances for one tree. Writes a YAML file for a
    person to read before it joins content/phrases/synthetic/. Needs the `anthropic`
    package and an API key (or `ant auth login`)."""
    import anthropic
    cmds, trees = load_commands(inter)
    passages = {json.loads(l)["id"]: json.loads(l)
                for l in (inter / "passages.jsonl").read_text().splitlines() if l}
    known = defaultdict(list)
    for c in cmds:
        known[c["key"]].append(c["phrase"])
    screens = []
    keys = []
    for nd in trees["nodes"]:
        if nd["tree"] != tree:
            continue
        keys.append(nd["key"])
        first = passages.get(nd["passages"][0], {}).get("text", "")[:300] if nd["passages"] else ""
        screens.append(f"- {nd['key']}: \"{nd['label']}\". Card begins: {first!r}. "
                       f"Known phrases: {known.get(nd['key'], [])}")
    schema = {"type": "object", "additionalProperties": False,
              "required": ["expect", "off_topic"],
              "properties": {
                  "expect": {"type": "object", "additionalProperties": False, "required": keys,
                             "properties": {k: {"type": "array", "items": {"type": "string"}}
                                            for k in keys}},
                  "off_topic": {"type": "object",
                                "additionalProperties": {"type": "array",
                                                         "items": {"type": "string"}}}}}
    client = anthropic.Anthropic()
    with client.beta.messages.stream(
            model=model, max_tokens=32000,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": GEN_PROMPT.format(
                n=n, n_off=n_off, screens="\n".join(screens))}]) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise SystemExit("the model declined; nothing written")
    data = json.loads(next(b.text for b in msg.content if b.type == "text"))
    out.write_text(f"# Generated by {model} for tree {tree!r}: READ BEFORE USE. Delete anything\n"
                   "# off-tone or wrong, then move it into content/phrases/synthetic/.\n"
                   + yaml.safe_dump(data, sort_keys=False, width=100, allow_unicode=False))
    return out
