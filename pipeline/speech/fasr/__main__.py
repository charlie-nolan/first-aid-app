import argparse
import json
import sys
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fasr")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lx = sub.add_parser("lexicon", help="build/append pipeline/plb/content/lexicon.yaml")
    lx.add_argument("--trees", nargs="*", default=[], type=Path)
    lx.add_argument("--extra", nargs="*", default=[], type=Path)
    lx.add_argument("--min-count", type=int, default=1)
    lx.add_argument("--dry-run", action="store_true")
    lx.add_argument("--renumber", action="store_true", help="one-time v2 rebuild (keeps ids 0-9); see lexicon.build")
    for name in ("train", "synth", "opendata", "evaluate", "whisper", "record", "export", "adapt", "devrec", "calibrate", "micmatch", "pagescore", "slot", "snap", "report", "export_int8", "tail"):
        sub.add_parser(name, add_help=False)
    a, rest = ap.parse_known_args(argv)
    if a.cmd != "lexicon":
        import importlib
        import os
        mod = {"whisper": "whisper_lora"}.get(a.cmd, a.cmd)
        from . import snap
        if a.cmd not in snap.CAPTURED or os.environ.get("FASR_NO_SNAP"):
            return importlib.import_module(f"fasr.{mod}").main(rest)
        tee = snap.Tee(sys.stdout); sys.stdout = tee
        try:
            return importlib.import_module(f"fasr.{mod}").main(rest)
        finally:
            sys.stdout = tee.real
            try:
                print("capture:", snap.render(tee.buf.getvalue(), a.cmd, snap.run_arg(rest),
                                              "python -m fasr " + " ".join([a.cmd] + rest)))
            except Exception as e:   # a capture must never break a run
                print("capture failed:", e)
    if a.cmd == "lexicon":
        from . import lexicon
        trees = a.trees or [lexicon.CONTENT / "trees"]
        rows, stats = lexicon.build(trees, a.extra, min_count=a.min_count, renumber=a.renumber)
        if not a.dry_run:
            lexicon.write(rows, version=2 if a.renumber else None)
        json.dump(stats, sys.stdout, indent=1)
        print()


if __name__ == "__main__":
    main()
