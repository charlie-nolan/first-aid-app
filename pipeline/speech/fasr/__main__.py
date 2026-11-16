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
    for name in ("train", "synth", "opendata", "evaluate", "whisper", "record", "export", "adapt", "devrec"):
        sub.add_parser(name, add_help=False)
    a, rest = ap.parse_known_args(argv)
    if a.cmd != "lexicon":
        import importlib
        mod = {"whisper": "whisper_lora"}.get(a.cmd, a.cmd)
        return importlib.import_module(f"fasr.{mod}").main(rest)
    if a.cmd == "lexicon":
        from . import lexicon
        trees = a.trees or [lexicon.CONTENT / "trees"]
        rows, stats = lexicon.build(trees, a.extra, min_count=a.min_count)
        if not a.dry_run:
            lexicon.write(rows)
        json.dump(stats, sys.stdout, indent=1)
        print()


if __name__ == "__main__":
    main()
