"""Test mode: prove the library only says what the manuals say, and every phrase lands right.

Checks, in order:
  1. sources: pinned hashes match, distribution is public release
  2. trees: content rules (citations, fallbacks, labels, no mushrooms) -- from build
  3. phrases: unique, ESP-SR-safe, under the limit; expected phrases open the right node
  4. passages: every passage is still a verbatim substring of its extracted section,
     plain ASCII, and every node has at least one (or is a no_match leaf)
  5. optional: `--reader-cmd` runs the packer's reader on library.bin and checks that
     each cmd id resolves to the same node the intermediate says, and that every node's
     passages decode verbatim
"""
from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import yaml

from .extract import load_sections
from .phrases import normalize
from .sources import verify
from .trees import Issue


def run_checks(root: Path, inter: Path, build_issues: list[Issue],
               reader_cmd: str | None = None, library: Path | None = None) -> list[Issue]:
    issues = list(build_issues)
    issues += [Issue("error", "sources", p) for p in verify(root, root / "sources.yaml")]

    trees = json.loads((inter / "trees.json").read_text())
    cmds = json.loads((inter / "phrases.json").read_text())
    passages = [json.loads(l) for l in (inter / "passages.jsonl").read_text().splitlines() if l]
    by_pid = {p["id"]: p for p in passages}
    key_of = {n["id"]: n["key"] for n in trees["nodes"]}

    # 3. expected phrase -> node
    exp_path = root / "content" / "phrases" / "expect.yaml"
    if exp_path.exists():
        expect = yaml.safe_load(exp_path.read_text()) or {}
        by_phrase = {c["phrase"]: c for c in cmds}
        for spoken, want in expect.items():
            c = by_phrase.get(normalize(spoken))
            if c is None:
                issues.append(Issue("error", "expect.yaml", f"{spoken!r} is not a registered phrase"))
            elif key_of.get(c["node"]) != want:
                issues.append(Issue("error", "expect.yaml",
                                    f"{spoken!r} opens {key_of.get(c['node'])}, expected {want}"))
    unrev = {n["id"] for n in trees["nodes"] if n.get("unreviewed")}
    by_id = {n["id"]: n for n in trees["nodes"]}
    for c in cmds:
        if c["node"] in unrev:
            issues.append(Issue("error", "phrases", f"{c['phrase']!r} opens unreviewed text"))
    for n in trees["nodes"]:
        if n.get("unreviewed") and (n["figure"] or n.get("emergency")):
            issues.append(Issue("error", n["key"], "unreviewed node has a picture or is emergency"))
        for ch in n["choices"]:
            if not n.get("unreviewed") and by_id[ch["to"]].get("unreviewed"):
                issues.append(Issue("error", n["key"], "reviewed topic links into unreviewed text"))
    # cited sections are pinned by content hash (content/cites.lock.yaml), because
    # heading-mode ids ("108.1", "2.h4") follow page order and can shift on re-extract
    lock_errs = check_cite_lock(root, trees)
    issues += lock_errs

    # refused passages (content/refused.yaml) never ship in a reviewed topic
    ref_path = root / "content" / "refused.yaml"
    for r in (yaml.safe_load(ref_path.read_text()) or []) if ref_path.exists() else []:
        for n in trees["nodes"]:
            if n.get("unreviewed"):
                continue
            for pid in n["passages"]:
                base = pid.split("#")[0].split("~")[0]
                p = by_pid.get(pid)
                if base == r["ref"] and (not r.get("text") or (p and r["text"] in p["text"])):
                    issues.append(Issue("error", n["key"], f"uses refused passage {pid}: {r['why']}"))
    for c in cmds:
        if c["node"] not in key_of:
            issues.append(Issue("error", "phrases", f"cmd {c['cmd_id']} points at missing node"))

    # 4. passages verbatim + ASCII + coverage
    sections = load_sections(sorted(p for p in (root / "build" / "extract").glob("*") if p.is_dir()))
    for p in passages:
        base = p["id"].split("#")[0].split("~")[0]
        sec = sections.get(base)
        pool = [] if sec is None else [sec["text"]] + [
            f"## {str(n.get('kind', 'NOTE')).upper()}\n{n['text']}" for n in sec.get("notices") or []]
        if not any(p["text"] in t for t in pool):
            issues.append(Issue("error", p["id"], "passage text is not verbatim from its section"))
        if any(not (c == "\n" or 32 <= ord(c) < 127) for c in p["text"]):
            issues.append(Issue("error", p["id"], "passage has non-ASCII characters"))
        if not p.get("citation"):
            issues.append(Issue("error", p["id"], "passage has no citation"))
    for n in trees["nodes"]:
        index_only = n.get("unreviewed") and n["choices"] and not n["passages"]
        if not n["passages"] and not n["no_match"] and not index_only:
            issues.append(Issue("error", n["key"], "node has no sourced passage"))
        for pid in n["passages"] + n["lookalikes"]:
            if pid not in by_pid:
                issues.append(Issue("error", n["key"], f"passage {pid} missing from passages.jsonl"))

    # 5. through the real reader
    if reader_cmd and not (library and library.exists()):
        issues.append(Issue("error", "reader", f"--reader-cmd given but {library} does not exist"))
    elif reader_cmd:
        for c in cmds:
            r = subprocess.run(shlex.split(reader_cmd) + [str(library), "cmd", str(c["cmd_id"])],
                               capture_output=True, text=True)
            got = r.stdout.strip()
            if r.returncode != 0 or got != str(c["node"]):
                issues.append(Issue("error", "reader", f"cmd {c['cmd_id']} ({c['phrase']!r}) -> "
                                                       f"{got or r.stderr.strip()}, expected {c['node']}"))
        # every node's passages must come back out of the ROM byte-for-byte
        for n in trees["nodes"]:
            r = subprocess.run(shlex.split(reader_cmd) + [str(library), "node", str(n["id"])],
                               capture_output=True, text=True)
            for pid in n["passages"] + n["lookalikes"]:
                if pid in by_pid and by_pid[pid]["text"] not in r.stdout:
                    issues.append(Issue("error", "reader", f"node {n['key']}: passage {pid} does "
                                                           "not decode verbatim from library.bin"))
    return issues


LOCK = Path("content") / "cites.lock.yaml"


def _section_hash(sec: dict) -> str:
    import hashlib
    blob = sec["text"] + "".join("\n" + str(n.get("text", "")) for n in sec.get("notices") or [])
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def reviewed_cites(trees: dict) -> set[str]:
    return {pid.split("#")[0].split("~")[0] for n in trees["nodes"] if not n.get("unreviewed")
            for pid in n["passages"]}


def _sections(root: Path) -> dict:
    return load_sections(sorted(p for p in (root / "build" / "extract").glob("*") if p.is_dir()))


def check_cite_lock(root: Path, trees: dict) -> list[Issue]:
    lock = yaml.safe_load((root / LOCK).read_text()) if (root / LOCK).exists() else {}
    lock = lock or {}
    secs = _sections(root)
    out = []
    for ref in sorted(reviewed_cites(trees)):
        sec = secs.get(ref)
        if sec is None:
            continue                                    # build already reports missing cites
        if ref not in lock:
            out.append(Issue("warning", ref, "cited section not in cites.lock.yaml (run plb lock "
                                             "after review)"))
        elif lock[ref]["hash"] != _section_hash(sec):
            out.append(Issue("error", ref, f"cited section changed since it was locked (was "
                                           f"{lock[ref]['starts']!r}); a re-extract may have "
                                           f"shifted ids. Fix the cite, then plb lock"))
    return out


def write_cite_lock(root: Path, trees: dict) -> int:
    secs = _sections(root)
    lock = {ref: {"hash": _section_hash(secs[ref]),
                  "starts": " ".join(secs[ref]["text"].split()[:8])}
            for ref in sorted(reviewed_cites(trees)) if ref in secs}
    (root / LOCK).write_text("# plb lock: content hash of every section a reviewed topic cites.\n"
                             + yaml.safe_dump(lock, sort_keys=True, width=100))
    return len(lock)
