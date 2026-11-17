"""Loads router topics, expert seeds and tree labels into labelled examples."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Example:
    text: str
    label: int
    weight: float = 1.0
    ctx: int = -1          # expert: node the user is on when they speak (-1 = none)


def load_tree(trees_dir, tid: str) -> dict | None:
    """trees_dir: one directory or a list (public trees, then trees_personal); first hit wins."""
    for d in trees_dir if isinstance(trees_dir, (list, tuple)) else [trees_dir]:
        p = Path(d) / f"{tid}.yaml"
        if p.exists():
            return yaml.safe_load(p.read_text())
    return None


def load_topics(topics_yaml: Path) -> dict:
    """topics.yaml plus an optional gitignored topics.personal.yaml next to it.
    Personal entries replace a topic's open/trees/pin (pointing it at personal trees, e.g.
    Aerie or Red Cross) and add train/test lines (replace: true swaps them); a new topic name adds a topic."""
    d = yaml.safe_load(Path(topics_yaml).read_text())
    pp = Path(topics_yaml).with_name("topics.personal.yaml")
    if pp.exists():
        for name, t in (yaml.safe_load(pp.read_text()) or {}).get("topics", {}).items():
            base = d["topics"].setdefault(name, {"train": [], "test": []})
            for k in ("open", "trees", "pin"):
                if k in t:
                    base[k] = t[k]
            for k in ("train", "test"):      # replace: true = this topic's lines are only the personal ones
                base[k] = (t.get(k, []) if t.get("replace") else base.get(k, []) + t.get(k, []))
    return d


def tree_texts(t: dict, top_only: bool = False) -> list[str]:
    """Title and card text. top_only: just the title and the first card with its choices
    (deep cards share words across trees: "improvised", "check circulation"...)."""
    out = [t.get("title", "")]
    nodes = t.get("nodes") or {}
    if top_only:
        root = t.get("root", next(iter(nodes), None))
        nodes = {root: nodes[root]} if root in nodes else {}
    for n in nodes.values():
        out.append(n.get("label", ""))
        out += [c.get("label", "") for c in n.get("choices") or []]
        out += [p if isinstance(p, str) else p.get("say", "") for p in n.get("phrases") or []]
    return [s for s in out if s]


class RouterData:
    REJECT = "reject"

    def __init__(self, topics_yaml: Path, trees_dir, scope: str | None = None):
        d = load_topics(topics_yaml)
        out, self.out_names = [], set()
        if scope:      # e.g. "first_aid": other topics' lines teach "reject" (not covered: say again / buttons)
            self.out_names = {n for n, t in d["topics"].items() if t.get("scope", "first_aid") != scope}
            out = [d["topics"][n] for n in self.out_names]
            d["topics"] = {n: t for n, t in d["topics"].items() if t.get("scope", "first_aid") == scope}
            for k in ("train", "test"):
                d["reject"][k] = d["reject"][k] + [u for t in out for u in t.get(k, [])]
        self.names = list(d["topics"]) + [self.REJECT]
        self.topics = d["topics"]
        self.reject = d["reject"]
        self.train, self.test, self.labels = [], [], []
        self.pins = [(k, g) for k, t in enumerate(self.topics.values()) for g in t.get("pin", [])]
        for k, (name, t) in enumerate(self.topics.items()):
            self.train += [Example(s, k) for s in t["train"]]
            self.test += [Example(s, k) for s in t["test"]]
            if trees_dir:
                for tid in t.get("trees", []):
                    tr = load_tree(trees_dir, tid)
                    if tr:
                        self.labels += [Example(s, k, 0.3) for s in tree_texts(tr, top_only=True)]
        r = len(self.names) - 1
        self.train += [Example(s, r) for s in self.reject["train"]]
        self.test += [Example(s, r) for s in self.reject["test"]]

    def all_text(self) -> list[str]:
        """Text the harvested lexicon may see: train, tree labels, pin words. Never test lines:
        a word heard only in a test sentence must stay unknown, as on the device."""
        return [e.text for e in self.train + self.labels] + [g for _, g in self.pins]


class ExpertData:
    """Classes: every node of the tree, then NONE (= hand back to the router)."""

    def __init__(self, expert_yaml: Path, trees_dir, router: RouterData | None):
        d = yaml.safe_load(Path(expert_yaml).read_text())
        self.tree_id = d["tree"]
        tr = load_tree(trees_dir, self.tree_id)
        if tr is None:
            raise FileNotFoundError(f"{self.tree_id}.yaml not in {trees_dir}")
        self.nodes = list(tr["nodes"])
        self.names = self.nodes + ["none"]
        self.none = len(self.nodes)
        idx = {k: i for i, k in enumerate(self.nodes)}
        self.parents: dict[int, list[int]] = {i: [] for i in range(len(self.nodes))}
        for k, n in tr["nodes"].items():
            for c in n.get("choices") or []:
                if c["to"] in idx:
                    self.parents[idx[c["to"]]].append(idx[k])
        self.same = {i: {i} for i in range(len(self.names))}
        for grp in d.get("same", []):
            g = {idx[x] for x in grp}
            for i in g:
                self.same[i] |= g
        self.root = idx[tr.get("root", self.nodes[0])]
        self.train, self.test, self.labels = [], [], []
        for k, s in d["nodes"].items():
            i = idx[k]
            self.train += [Example(u, i) for u in s.get("train", [])]
            self.test += [Example(u, i) for u in s.get("test", [])]
        for k, n in tr["nodes"].items():                      # card labels and the choice text that leads there
            self.labels.append(Example(n.get("label", ""), idx[k], 0.5))
            for c in n.get("choices") or []:
                if c["to"] in idx:
                    self.labels.append(Example(c["label"], idx[c["to"]], 0.5))
        if router:                                              # other topics' words -> NONE
            mine = {k for k, n in enumerate(router.names[:-1]) if router.topics[n].get("open") == self.tree_id}
            for e in router.train:
                if e.label not in mine:
                    self.train.append(Example(e.text, self.none, 0.5))
            for e in router.test:
                if e.label not in mine:
                    self.test.append(Example(e.text, self.none))
        self.tree = tr

    def ctx_for(self, label: int, rng) -> int:
        """Where the user plausibly is when they say something that leads to `label`."""
        if label == self.none:
            return rng.randrange(len(self.nodes))
        p = self.parents.get(label) or [self.root]
        r = rng.random()
        if r < 0.6:
            return rng.choice(p)
        if r < 0.8:
            return self.root
        return rng.randrange(len(self.nodes))

    def all_text(self) -> list[str]:
        """Train + tree labels only (see RouterData.all_text)."""
        return [e.text for e in self.train + self.labels]
