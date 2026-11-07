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
    for c in cmds:
        if c["node"] not in key_of:
            issues.append(Issue("error", "phrases", f"cmd {c['cmd_id']} points at missing node"))

    # 4. passages verbatim + ASCII + coverage
    sections = load_sections(sorted(p for p in (root / "build" / "extract").glob("*") if p.is_dir()))
    for p in passages:
        base = p["id"].split("#")[0]
        sec = sections.get(base)
        if sec is None or p["text"] not in sec["text"]:
            issues.append(Issue("error", p["id"], "passage text is not verbatim from its section"))
        if any(not (c == "\n" or 32 <= ord(c) < 127) for c in p["text"]):
            issues.append(Issue("error", p["id"], "passage has non-ASCII characters"))
        if not p.get("citation"):
            issues.append(Issue("error", p["id"], "passage has no citation"))
    for n in trees["nodes"]:
        if not n["passages"] and not n["no_match"]:
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
