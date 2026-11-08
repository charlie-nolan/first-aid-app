"""plb: Pocket Librarian Builder.

    plb fetch [ID]                 try each manual's URLs (needs network access to the hosts)
    plb ingest ID FILE.pdf         add a PDF you downloaded yourself; pins its sha256
    plb extract [ID ...]           PDF -> build/extract/ID/{sections,figures}.json + figure PNGs
    plb build                      trees + sections -> build/intermediate/ (the packer's input)
    plb test [--reader-cmd CMD]    build, then run every check; non-zero exit on any error
    plb pack --packer CMD          build + test, then hand build/intermediate to the packer
    plb review [--out DIR]         build, then write one Markdown review page per tree
    plb verbatim [--all] [--judge]  re-read cited pages from the PDFs; card text word for word?
    plb voice [--json]             score synthetic utterances against the phrase list
    plb show SECTION_ID            print an extracted section (to pick citations)
    plb grep WORDS                 search extracted sections (to find what to cite)
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

from . import sources as src
from .build import build
from .check import run_checks
from .extract import extract, load_sections
from .web import extract_web


def _root(a) -> Path:
    return Path(a.root).resolve()


def _report(issues) -> int:
    errs = [i for i in issues if i.level == "error"]
    for i in sorted(issues, key=lambda i: (i.level != "error", i.where)):
        print(i)
    print(f"{len(errs)} error(s), {len(issues) - len(errs)} warning(s)")
    return 1 if errs else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="plb", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".", help="repo root (has sources.yaml)")
    ap.add_argument("--personal", action="store_true",
                    help="also use the gitignored sources.personal.yaml books (personal device only)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch"); f.add_argument("id", nargs="?")
    i = sub.add_parser("ingest"); i.add_argument("id"); i.add_argument("pdf")
    e = sub.add_parser("extract"); e.add_argument("ids", nargs="*")
    e.add_argument("--no-figures", action="store_true")
    e.add_argument("--figure-cmd", help="external extractor: CMD PDF OUTDIR, must write figures.json")
    sub.add_parser("build")
    sub.add_parser("lock", help="pin the content hash of every section a reviewed topic cites")
    t = sub.add_parser("test"); t.add_argument("--reader-cmd"); t.add_argument("--library")
    p = sub.add_parser("pack"); p.add_argument("--packer", required=True,
                                               help="CMD INTERMEDIATE_DIR OUT.bin")
    p.add_argument("--out", default=None, help="default build/library.bin "
                                                 "(build/library_personal.bin with --personal)")
    rv = sub.add_parser("review"); rv.add_argument("--out", default="content/review")
    vb = sub.add_parser("verbatim", help="compare card passages with the original PDF pages")
    vb.add_argument("--all", action="store_true", help="also the unreviewed reference shelf")
    vb.add_argument("--judge", action="store_true", help="ask Claude about each MISMATCH (needs API key)")
    vb.add_argument("--only", nargs="*", help="passage id prefixes, e.g. ATP4-02.11/4-4")
    vb.add_argument("--out", default="build/verbatim.md")
    vc = sub.add_parser("voice", help="stress-test the voice phrase list with synthetic utterances")
    vc.add_argument("--utterances", default="content/phrases/synthetic")
    vc.add_argument("--out", default="build/voice-report.md")
    vc.add_argument("--generate", metavar="TREE", help="ask Claude for new utterances for TREE "
                    "(writes build/synthetic-TREE.yaml to review; needs an API key)")
    vc.add_argument("--threshold", type=float, default=None, help="recogniser stand-in cutoff")
    vc.add_argument("--min-accuracy", type=float, default=None,
                    help="fail if fewer than this share of in-scope utterances land right")
    s = sub.add_parser("show"); s.add_argument("section")
    g = sub.add_parser("grep"); g.add_argument("words", nargs="+")
    a = ap.parse_args(argv)
    root = _root(a)
    manifest = root / "sources.yaml"

    if a.cmd == "fetch":
        for line in src.fetch(root, manifest, a.id):
            print(line)
        return 0
    if a.cmd == "ingest":
        m = src.ingest(root, manifest, a.id, Path(a.pdf))
        print(f"{m.id}: sha256 {m.sha256}\n  distribution: {m.distribution}")
        return 0
    if a.cmd == "extract":
        rc = 0
        for m in src.load_manifest(manifest, a.personal):
            if a.ids and m.id not in a.ids:
                continue
            pdf = m.path(root)
            if m.raw.get("kind") == "web" and pdf.exists():
                out = root / "build" / "extract" / m.id
                secs = extract_web(root, m.raw, out)
                print(f"{m.id}: {len(secs)} sections from {len(m.raw['pages'])} web pages -> {out}")
                continue
            if not pdf.exists():
                if a.ids:
                    print(f"{m.id}: {pdf} not found (plb ingest first)"); rc = 1
                continue
            out = root / "build" / (src.PERSONAL_EXTRACT if m.raw.get("personal") else "extract") / m.id
            secs, figs = extract(pdf, m.id, out, figures=not (a.no_figures or a.figure_cmd),
                                 numbering=m.raw.get("numbering", "dash"),
                                 skip_pages=tuple(m.raw.get("skip_pages") or ()),
                                 table_pages=(m.raw["table_pages"] if m.raw.get("table_pages") == "all"
                                              else tuple(m.raw.get("table_pages") or ())),
                                 chapter_pattern=m.raw.get("chapter_pattern"),
                                 margins=m.raw.get("margins", "fixed"),
                                 columns=bool(m.raw.get("columns")))
            if a.figure_cmd:
                subprocess.run(shlex.split(a.figure_cmd) + [str(pdf), str(out)], check=True)
                figs = json.loads((out / "figures.json").read_text())
            print(f"{m.id}: {len(secs)} sections, {len(figs)} figures -> {out}")
        return rc
    if a.cmd == "lock":
        from .check import write_cite_lock
        out, issues = build(root, personal=a.personal)
        n = write_cite_lock(root, json.loads((out / "trees.json").read_text()), a.personal)
        print(f"locked {n} cited sections -> content/cites.lock.yaml"
              + (" + content/cites.personal.lock.yaml" if a.personal else ""))
        return _report(issues)
    if a.cmd in ("build", "test", "pack"):
        out, issues = build(root, personal=a.personal)
        if a.cmd == "build":
            print(f"intermediate -> {out}")
            return _report(issues)
        lib = Path(a.library) if getattr(a, "library", None) else root / "build" / "library.bin"
        issues = run_checks(root, out, issues, getattr(a, "reader_cmd", None), lib, a.personal)
        rc = _report(issues)
        if a.cmd == "pack" and rc == 0:
            dest = a.out or ("build/library_personal.bin" if a.personal else "build/library.bin")
            r = subprocess.run(shlex.split(a.packer) + [str(out), str(root / dest)])
            return r.returncode
        return rc
    if a.cmd == "review":
        from .review import write_review
        out, issues = build(root)
        for f in write_review(out, root / a.out):
            print(f)
        return _report(issues)
    if a.cmd == "verbatim":
        from . import verbatim as vb
        from .check import _sections
        out, issues = build(root, personal=a.personal)
        manuals = {m.id: m.raw for m in src.load_manifest(manifest, a.personal)}
        secs, _ = _sections(root, a.personal)
        found = vb.check_passages(root, out, manuals, secs, only_reviewed=not a.all, ids=a.only)
        if a.judge:
            print(f"judged {vb.judge(found, root, out, manuals, secs)} mismatches")
        dest = root / a.out
        print(vb.write_report(found, dest), "->", dest)
        for f in found:
            if f.level in ("MISMATCH", "NO-SOURCE"):
                print(f"{f.level:9} {f.passage:28} {(f.judge or {}).get('verdict', '')}")
        return 1 if any(f.level == "MISMATCH" for f in found) else 0
    if a.cmd == "voice":
        from . import voicetest as vt
        out, issues = build(root, personal=a.personal)
        if a.generate:
            print("wrote", vt.generate(out, a.generate, root / "build" / f"synthetic-{a.generate}.yaml"))
            return 0
        rep = vt.run(root, out, root / a.utterances, thr=a.threshold or vt.THRESHOLD)
        dest = root / a.out
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(vt.report_md(rep))
        print(vt.summary(rep), "->", dest)
        if a.min_accuracy is not None and rep["in_scope_accuracy"] < a.min_accuracy:
            return 1
        return 1 if rep["unsafe"] or rep["off_step"] else 0
    if a.cmd in ("show", "grep"):
        secs = load_sections(sorted(p for p in (root / "build" / "extract").glob("*") if p.is_dir()))
        if a.cmd == "show":
            s = secs.get(a.section)
            print(json.dumps(s, indent=1) if s else "not found")
            return 0 if s else 1
        words = [w.lower() for w in a.words]
        for sid, s in secs.items():
            low = s["text"].lower()
            if all(w in low for w in words):
                print(f"{sid:28} {s['heading'][:30]:30} {s['text'][:90]!r}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
