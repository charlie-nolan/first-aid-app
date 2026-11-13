"""Draft diagnostic trees from the manuals with a LOCAL model (Ollama on the desktop GPU).

For each topic in tree_topics.yaml the model gets the full text of the listed sections
and returns a tree as JSON constrained by a schema: it can only cite those sections (by
number), so it can't invent a source or write body text. It writes the questions'
navigation labels, the answers and the voice phrases. plb then builds the draft with the
real rules (verbatim cites, labels <= 40 chars, at most 4 answers, kit/improvised needs a
"nothing" branch, phrase clashes); any errors go back to the model, up to --tries times.

Nothing here ships. Drafts land in content/drafts/<id>.yaml with a review page
(content/drafts/review/<id>.md). Charlie reads them, edits, and moves a signed-off tree
into content/trees/ himself.

    python draft_trees.py [--model qwen3:30b-a3b-instruct-2507-q4_K_M] [--only cpr,choking]
    python draft_trees.py --personal --topics tree_topics.personal.yaml   # Aerie, local only
"""
from __future__ import annotations

import argparse, datetime as dt, json, shutil, sys, tempfile, textwrap
from pathlib import Path

import requests
import yaml

HERE = Path(__file__).resolve().parent
PLB = HERE.parent / "plb"
sys.path.insert(0, str(PLB))
from plb.build import build  # noqa: E402
from plb.check import _sections  # noqa: E402
from plb.review import write_review  # noqa: E402
from plb.voicetest import load_utterances  # noqa: E402

RULES = """\
You arrange first-aid passages from official manuals into a small decision tree for a
pocket e-ink device used by a hiker with no phone signal. Hard rules:
- You never write advice. The device shows only the cited passages. You choose which of
  the numbered SOURCES each card shows (cite by number) and write short navigation text.
- Every node cites at least one source. A source may appear on more than one node.
- label: the card's heading, a short question or action, at most 40 characters, plain ASCII.
  The root node's label is the first question (e.g. "Is the person breathing?").
- choices: at most 4 answers per node, each label at most 40 characters. One button press
  per answer, so keep the tree shallow (3 presses or fewer to any card where possible).
- kind: "kit" (uses gear like a splint or tourniquet), "improvised" (makeshift gear),
  "nothing" (no gear), or "next" (anything else). A node with a kit or improvised answer
  MUST also have a "nothing" answer, and it is listed last.
- Every node except the root must be reachable from the root. Leaf nodes have no choices.
- phrases: how a scared hiker or parent would ASK for this card out loud, lowercase, plain
  everyday words, 3-12 per node and 15-30 for the root. No military or violent wording.
  Don't reuse a phrase on two nodes, and avoid phrases that sound like another topic.
- Node keys: short lowercase words with underscores (start, signs, treat, ...).
"""

SCHEMA_NODE = {
    "type": "object",
    "properties": {
        "key": {"type": "string"},
        "label": {"type": "string"},
        "cite": {"type": "array", "items": {"type": "integer"}},
        "phrases": {"type": "array", "items": {"type": "string"}},
        "choices": {"type": "array", "items": {
            "type": "object",
            "properties": {"label": {"type": "string"}, "to": {"type": "string"},
                           "kind": {"type": "string", "enum": ["kit", "improvised", "nothing", "next"]}},
            "required": ["label", "to", "kind"]}},
    },
    "required": ["key", "label", "cite", "phrases", "choices"],
}
SCHEMA = {"type": "object",
          "properties": {"root_phrases": {"type": "array", "items": {"type": "string"}},
                         "nodes": {"type": "array", "items": SCHEMA_NODE}},
          "required": ["root_phrases", "nodes"]}


def cite_text(c, sections) -> tuple[str, str]:
    ref = c["ref"] if isinstance(c, dict) else c
    s = sections.get(ref)
    if not s:
        return ref, "(MISSING: not an extracted section)"
    t = s["text"]
    if isinstance(c, dict):
        a = t.find(c.get("from", "")); b = t.find(c.get("to", ""), max(a, 0))
        if a >= 0 and b >= 0:
            t = t[a:b + len(c["to"])]
    return ref, f"[{s['heading']}] {t}"


def ask(model: str, url: str, messages: list[dict]) -> dict:
    r = requests.post(f"{url}/api/chat", timeout=1800, json={
        "model": model, "messages": messages, "format": SCHEMA, "stream": False,
        "options": {"temperature": 0.3, "num_ctx": 16384}})
    r.raise_for_status()
    return json.loads(r.json()["message"]["content"])


def to_tree(topic: dict, ans: dict) -> dict:
    cites = topic["cites"]
    nodes, order = {}, []
    for n in ans["nodes"]:
        key = n["key"].strip().lower().replace(" ", "_") or f"n{len(order)}"
        node = {"label": n["label"].strip(),
                "cite": [cites[i - 1] for i in n["cite"] if 1 <= i <= len(cites)]}
        if n.get("phrases") and order:
            node["phrases"] = [p.strip().lower() for p in n["phrases"] if p.strip()]
        if n.get("choices"):
            node["choices"] = [{"label": c["label"].strip(), "to": c["to"].strip().lower().replace(" ", "_"),
                                **({"kind": c["kind"]} if c["kind"] != "next" else {})} for c in n["choices"]]
        nodes[key] = node; order.append(key)
    tree = {"id": topic["id"], "title": topic["title"], "shelf": topic.get("shelf", "first-aid")}
    if topic.get("emergency"):
        tree["emergency"] = True
    tree.update({"root": order[0] if order else "start",
                 "phrases": [p.strip().lower() for p in ans.get("root_phrases", []) if p.strip()],
                 "nodes": nodes})
    return tree


def validate(tree: dict, personal: bool) -> tuple[list[str], Path, Path]:
    """Build the library with this draft added; return the draft's errors."""
    tmp = Path(tempfile.mkdtemp(prefix="draft-"))
    tdir = tmp / "trees"; tdir.mkdir()
    for f in (PLB / "content" / "trees").glob("*.yaml"):
        if f.stem != tree["id"]:
            shutil.copy(f, tdir / f.name)
    name = f"{tree['id']}.yaml"
    (tdir / name).write_text(yaml.safe_dump(tree, sort_keys=False, allow_unicode=False, width=110))
    out, issues = build(PLB, out=tmp / "inter", tree_dir=tdir, personal=personal)
    mine = [str(i) for i in issues if name in i.where or tree["id"] + "/" in i.where]
    return mine, tmp, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topics", type=Path, default=HERE / "tree_topics.yaml")
    ap.add_argument("--only", default="")
    ap.add_argument("--model", default="qwen3:30b-a3b-instruct-2507-q4_K_M")
    ap.add_argument("--ollama", default="http://127.0.0.1:11434")
    ap.add_argument("--tries", type=int, default=4)
    ap.add_argument("--personal", action="store_true")
    a = ap.parse_args()

    sections, _ = _sections(PLB, a.personal)
    utts = load_utterances(PLB / "content" / "phrases" / "synthetic")
    dest = PLB / "content" / ("drafts_personal" if a.personal else "drafts")
    (dest / "review").mkdir(parents=True, exist_ok=True)
    topics = yaml.safe_load(a.topics.read_text())["topics"]
    if a.only:
        topics = [t for t in topics if t["id"] in a.only.split(",")]

    for topic in topics:
        print(f"\n=== {topic['id']}: {topic['title']}", flush=True)
        src = "\n\n".join(f"SOURCE {i}: {ref}\n{txt}" for i, (ref, txt) in
                          enumerate((cite_text(c, sections) for c in topic["cites"]), 1))
        asks = [u["text"] for u in utts if u["off_topic"] and u["topic"] == topic["id"]]
        prompt = (f"TOPIC: {topic['title']}\n"
                  + (f"PLAN NOTES: {topic['notes']}\n" if topic.get("notes") else "")
                  + (f"DO NOT USE: {'; '.join(topic['avoid'])}\n" if topic.get("avoid") else "")
                  + (f"HOW PEOPLE ASK (examples, don't copy all): {'; '.join(asks)}\n" if asks else "")
                  + f"\n{src}\n\nReturn the tree as JSON. The first node is the root.")
        msgs = [{"role": "system", "content": RULES}, {"role": "user", "content": prompt}]
        errs, tree, out = ["no answer"], None, None
        for attempt in range(1, a.tries + 1):
            try:
                ans = ask(a.model, a.ollama, msgs)
            except Exception as e:
                print("  model error:", e); errs = [f"model error: {e}"]; continue
            tree = to_tree(topic, ans)
            errs, tmp, out = validate(tree, a.personal)
            print(f"  try {attempt}: {len(tree['nodes'])} nodes, {len(errs)} error(s)", flush=True)
            if not errs:
                break
            msgs += [{"role": "assistant", "content": json.dumps(ans)},
                     {"role": "user", "content": "The builder rejected it:\n" + "\n".join(errs[:20])
                      + "\nFix every error and return the whole tree again."}]
        if not tree:
            continue
        head = textwrap.dedent(f"""\
            # DRAFT for Charlie's review: made {dt.date.today()} by {a.model} (local) with
            # pipeline/gpu/draft_trees.py. On-screen text is only the cited passages; labels and
            # phrases are the model's. NOT signed off: edit, then move to content/{'trees_personal' if a.personal else 'trees'}/.
            """)
        if errs:
            head += "# STILL FAILING plb:\n" + "".join(f"#   {e}\n" for e in errs)
        (dest / f"{topic['id']}.yaml").write_text(head + yaml.safe_dump(tree, sort_keys=False, width=110))
        if out and not errs:
            for f in write_review(out, dest / "review"):
                if f.stem != topic["id"]:
                    f.unlink()
        print(f"  -> {dest / (topic['id'] + '.yaml')}" + (" (with errors)" if errs else ""), flush=True)


if __name__ == "__main__":
    main()
