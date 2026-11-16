"""Build the shared lexicon (pipeline/plb/content/lexicon.yaml).

The lexicon is the word list the custom recognizer may output and the router reads.
Contract (agreed 2026-11-16 with the voice router thread, see pipeline/voice_router/vr/lexicon.py):
  ids 0..2047 (raised from 1023 on 2026-11-16, agreed with the router), append-only; 0 <unk>, 1-4 one..four, 5 yes, 6 no, 7 back, 8 again, 9 home.
  class byte: 0 other, 1 body_part, 2 mechanism, 3 symptom, 4 negation, 5 quantity.
Each word also carries its sound spellings (ARPAbet phones, stress removed), which is what
lets the recognizer learn sounds once and treat the vocabulary as data.

Sources, in this order (earlier sources win the free ids first):
  1. pipeline/plb/content/speech_core.yaml   curated first-aid words, with classes
  2. tree files (--trees DIR ...)            titles, phrases, node labels, choice labels
  3. voice seeds (content/voice)             `train` sentences only, never `test`
  4. --extra FILE/DIR ...                    generated sentences (one per line, or yaml lists)
Held-out sentences (voice `test`, phrases/synthetic) are only used to report coverage.

    python -m fasr lexicon --trees ~/first-aid-build/pipeline/plb/content/trees [--extra out/generated]
"""
from __future__ import annotations

import re
import subprocess
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
CONTENT = ROOT / "pipeline" / "plb" / "content"
LEXICON = CONTENT / "lexicon.yaml"
CORE = CONTENT / "speech_core.yaml"

CAP = 2048
RESERVED = ["<unk>", "one", "two", "three", "four", "yes", "no", "back", "again", "home"]
CLASSES = {"other": 0, "body_part": 1, "mechanism": 2, "symptom": 3, "negation": 4, "quantity": 5}
# when a core word sits in several groups, the first group here wins
CLASS_PRIORITY = ["negation", "body_part", "symptom", "mechanism", "quantity", "other"]
MAX_PRONS = 3

# same tokenizer as vr.lexicon.tokenize (tests check they agree)
_tok = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_NUM = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six",
        "7": "seven", "8": "eight", "9": "nine", "10": "ten", "911": "nine one one"}


def tokenize(text: str) -> list[str]:
    return _tok.findall(text.lower().replace("’", "'"))


# ---------------------------------------------------------------- sound spellings
_cmu = None


def _cmudict():
    global _cmu
    if _cmu is None:
        import cmudict
        _cmu = cmudict.dict()
    return _cmu


_hp = None


def _hand():
    global _hp
    if _hp is None:
        _hp = hand_prons()
    return _hp


def strip_stress(pron: list[str]) -> list[str]:
    return [re.sub(r"\d", "", p) for p in pron]


# eSpeak IPA -> ARPAbet, only used for words CMUdict lacks (desktop has espeak-ng)
_IPA = [("tʃ", "CH"), ("dʒ", "JH"), ("aɪ", "AY"), ("aʊ", "AW"), ("ɔɪ", "OY"), ("eɪ", "EY"),
        ("oʊ", "OW"), ("əʊ", "OW"), ("ɜː", "ER"), ("ɚ", "ER"), ("ɝ", "ER"), ("iː", "IY"), ("uː", "UW"),
        ("ɑː", "AA"), ("ɔː", "AO"), ("ɪ", "IH"), ("ɛ", "EH"), ("æ", "AE"), ("ʌ", "AH"), ("ə", "AH"),
        ("ʊ", "UH"), ("ɑ", "AA"), ("ɒ", "AA"), ("ɔ", "AO"), ("i", "IY"), ("u", "UW"), ("e", "EH"),
        ("θ", "TH"), ("ð", "DH"), ("ʃ", "SH"), ("ʒ", "ZH"), ("ŋ", "NG"), ("ɹ", "R"), ("r", "R"),
        ("ɾ", "T"), ("j", "Y"), ("w", "W"), ("h", "HH"), ("p", "P"), ("b", "B"), ("t", "T"),
        ("d", "D"), ("k", "K"), ("ɡ", "G"), ("g", "G"), ("f", "F"), ("v", "V"), ("s", "S"),
        ("z", "Z"), ("m", "M"), ("n", "N"), ("l", "L"), ("ɫ", "L"), ("o", "OW"), ("a", "AA")]


def espeak_pron(word: str) -> list[str] | None:
    try:
        ipa = subprocess.run(["espeak-ng", "-q", "--ipa", "-v", "en-us", word],
                             capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    ipa = re.sub(r"[ˈˌː\s_-]", lambda m: "ː" if m.group() == "ː" else "", ipa)
    out, i = [], 0
    while i < len(ipa):
        for k, v in _IPA:
            if ipa.startswith(k, i):
                out.append(v)
                i += len(k)
                break
        else:
            i += 1
    return out or None


def prons(word: str) -> tuple[list[list[str]], str]:
    """Sound spellings for a word and where they came from (cmudict / espeak / none)."""
    if word == "<unk>":
        return [], "none"
    hand = _hand()
    if word in hand:
        return hand[word][:MAX_PRONS], "hand"
    d = _cmudict()
    w = word
    if w in d:
        seen, out = set(), []
        for p in d[w]:
            s = tuple(strip_stress(p))
            if s not in seen:
                seen.add(s)
                out.append(list(s))
        return out[:MAX_PRONS], "cmudict"
    if word in _NUM:
        ps = [prons(x)[0][0] for x in _NUM[word].split()]
        return [[p for q in ps for p in q]], "cmudict"
    e = espeak_pron(word)
    return ([e], "espeak") if e else ([], "none")


# ---------------------------------------------------------------- sources
def load_core(path: Path = CORE) -> dict[str, int]:
    d = yaml.safe_load(path.read_text())
    out: dict[str, int] = {}
    for cname in CLASS_PRIORITY:  # (the `phones` key holds hand spellings, see hand_prons)
        for line in d.get(cname, []) or []:
            for w in (x.strip().lower() for x in str(line).split(",")):
                if w and w not in out:
                    out[w] = CLASSES[cname]
    return out


def hand_prons(path: Path = CORE) -> dict[str, list[list[str]]]:
    d = yaml.safe_load(path.read_text()) or {}
    return {str(w).lower(): [str(p).split() for p in ps] for w, ps in (d.get("phones") or {}).items()}


def tree_texts(tree_dirs: list[Path]) -> list[str]:
    out = []
    for d in tree_dirs:
        for f in sorted(Path(d).expanduser().glob("*.yaml")):
            try:
                t = yaml.safe_load(f.read_text()) or {}
            except yaml.YAMLError:
                continue
            if not isinstance(t, dict) or "nodes" not in t:
                continue
            out.append(str(t.get("title", "")))
            out += [str(p) for p in t.get("phrases", []) or []]
            for n in (t.get("nodes") or {}).values():
                if not isinstance(n, dict):
                    continue
                out.append(str(n.get("label", "")))
                out += [str(p) for p in n.get("phrases", []) or []]
                for c in n.get("choices", []) or []:
                    if isinstance(c, dict):
                        out.append(str(c.get("label", "")))
    return out


def _voice_split(which: str) -> list[str]:
    out = []
    vdir = CONTENT / "voice"
    if not vdir.exists():
        return out
    for f in [vdir / "topics.yaml", *sorted((vdir / "experts").glob("*.yaml"))]:
        if not f.exists():
            continue
        d = yaml.safe_load(f.read_text()) or {}
        groups = [d.get("reject", {})] + list((d.get("topics") or {}).values()) + list((d.get("nodes") or {}).values())
        for g in groups:
            if isinstance(g, dict):
                out += [str(s) for s in g.get(which, []) or []]
    return out


def held_out_texts() -> list[str]:
    out = _voice_split("test")
    for f in sorted((CONTENT / "phrases" / "synthetic").glob("*.yaml")):
        d = yaml.safe_load(f.read_text()) or {}
        for v in (d.get("expect") or {}).values():
            out += [str(s).split("|")[0] for s in v or []]
    return out


def extra_texts(paths: list[Path]) -> list[str]:
    out = []
    for p in paths:
        p = Path(p).expanduser()
        files = sorted(p.rglob("*")) if p.is_dir() else [p]
        for f in files:
            if "test" in f.name or "eval" in f.name:     # held-out sets (e.g. agy_test.yaml) never feed the lexicon
                continue
            if f.suffix in (".yaml", ".yml"):
                def walk(x):
                    if isinstance(x, str):
                        out.append(x)
                    elif isinstance(x, list):
                        for y in x:
                            walk(y)
                    elif isinstance(x, dict):
                        for k, y in x.items():
                            if k != "test":
                                walk(y)
                walk(yaml.safe_load(f.read_text()))
            elif f.suffix in (".txt", ".jsonl") or f.suffix == "":
                if f.is_file():
                    out += f.read_text().splitlines()
    return out


# ---------------------------------------------------------------- build
def load_existing(path: Path = LEXICON) -> list[dict]:
    if not path.exists():
        return []
    rows = sorted(yaml.safe_load(path.read_text())["words"], key=lambda r: r["id"])
    for i, r in enumerate(rows):
        if r["id"] != i:
            raise ValueError(f"{path}: ids must be 0..n-1 with no gaps (id {r['id']} at row {i})")
    if [r["word"] for r in rows[: len(RESERVED)]] != RESERVED:
        raise ValueError(f"{path}: reserved ids changed")
    return rows


def build(tree_dirs: list[Path], extra: list[Path], existing: list[dict] | None = None,
          min_count: int = 1) -> tuple[list[dict], dict]:
    core = load_core()
    rows = [dict(r) for r in (existing if existing is not None else load_existing())]
    have = {r["word"] for r in rows}

    def add(w: str, src: str):
        if w in have or len(rows) >= CAP:
            return
        rows.append({"id": len(rows), "word": w, "class": core.get(w, 0), "src": src})
        have.add(w)

    for w in RESERVED:
        add(w, "reserved")
    for w in core:                      # curated words first, in file order
        add(w, "core")
    texts = {"trees": tree_texts(tree_dirs), "voice": _voice_split("train"), "extra": extra_texts(extra)}
    for src, tx in texts.items():       # then by frequency within each source
        c = Counter(w for t in tx for w in tokenize(t))
        for w, n in c.most_common():
            if n >= (min_count if src == "extra" else 1):   # generated text: only words seen often
                add(w, src)
    # classes and sounds (classes for reserved/core words follow the core file)
    for r in rows:
        if r["word"] in core:
            r["class"] = core[r["word"]]
        ps, how = prons(r["word"])
        r["phones"] = [" ".join(p) for p in ps]
        r["g2p"] = how
    held = held_out_texts()
    toks = [w for t in held for w in tokenize(t)]
    oov = Counter(w for w in toks if w not in have)
    stats = {
        "words": len(rows), "cap": CAP,
        "texts": {k: len(v) for k, v in texts.items()},
        "held_out_tokens": len(toks),
        "held_out_oov_rate": round(sum(oov.values()) / max(1, len(toks)), 4),
        "held_out_oov_top": oov.most_common(30),
        "no_phones": [r["word"] for r in rows if not r["phones"] and r["id"] != 0],
        "from_espeak": [r["word"] for r in rows if r["g2p"] == "espeak"],
        "dropped_at_cap": len(rows) >= CAP,
    }
    return rows, stats


HEADER = """\
# Shared lexicon: the words the custom speech recognizer can output and the voice router reads.
# GENERATED by `python -m fasr lexicon` (pipeline/speech). Ids are APPEND-ONLY: never renumber,
# never delete a row (the router's word table and the recognizer's decoder index by id).
# To retire a word, leave it in place; to fix a class or a sound spelling, edit that row.
# Contract: ids 0..2047; 0 <unk>, 1-4 one..four, 5 yes, 6 no, 7 back, 8 again, 9 home.
# class: 0 other, 1 body_part, 2 mechanism, 3 symptom, 4 negation, 5 quantity.
# phones: ARPAbet without stress (CMUdict; `g2p: espeak` = machine guess, worth a check).
"""


def write(rows: list[dict], path: Path = LEXICON):
    """One row per line, so diffs show exactly which words were appended or edited."""
    import json

    def q(x):  # JSON is valid YAML flow style
        return json.dumps(x)
    lines = [HEADER.rstrip("\n"), "version: 1", f"cap: {CAP}", "words:"]
    for r in rows:
        lines.append(f"- {{id: {r['id']}, word: {q(r['word'])}, class: {r['class']}, "
                     f"phones: {q(r['phones'])}, g2p: {r['g2p']}, src: {r['src']}}}")
    path.write_text("\n".join(lines) + "\n")
