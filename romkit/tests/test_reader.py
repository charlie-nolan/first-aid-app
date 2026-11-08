"""Round trip through the C reader: pack the bench cartridge, decode it with reader/cart.c.

    cd romkit && python -m pytest -q tests

Checks that every text and picture decodes byte-for-byte, that every voice command
lands on its node, and that the committed firmware/library/library.bin is not stale.
Needs a C compiler and the packer deps (numpy, Pillow, freetype-py, uharfbuzz).
"""
from __future__ import annotations

import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROMKIT = Path(__file__).resolve().parents[1]
REPO = ROMKIT.parent
BENCH = REPO / "firmware" / "library"
sys.path.insert(0, str(ROMKIT))

import pack  # noqa: E402


def _cc(tmp: Path, name: str, *srcs: str) -> Path:
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if cc is None:
        pytest.skip("no C compiler")
    exe = tmp / name
    subprocess.run([cc, "-O2", "-std=c99", "-Wall", "-o", str(exe),
                    *[str(ROMKIT / "reader" / s) for s in srcs]], check=True)
    return exe


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rom")
    lib = tmp / "library.bin"
    blob, *_ = pack.pack(str(BENCH / "test"), str(lib))
    return dict(tmp=tmp, lib=lib, cartcli=_cc(tmp, "cartcli", "cart.c", "cartcli.c"),
                test_cart=_cc(tmp, "test_cart", "cart.c", "test_cart.c"))


def test_reader_compiles_warning_free(tmp_path):
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if cc is None:
        pytest.skip("no C compiler")
    # the device reader itself must stay clean C99 with no warnings
    subprocess.run([cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-Os", "-c",
                    str(ROMKIT / "reader" / "cart.c"), "-o", str(tmp_path / "cart.o")], check=True)


def test_committed_bench_library_is_current(built):
    """firmware/library/library.bin must be what make_test_library.py packs today."""
    assert built["lib"].read_bytes() == (BENCH / "library.bin").read_bytes(), (
        "firmware/library/library.bin is stale: run python3 firmware/tools/make_test_library.py "
        "and commit the result")


def test_every_text_and_picture_decodes(built):
    tmp = built["tmp"]
    r = subprocess.run([str(built["test_cart"]), str(built["lib"]), str(tmp / "texts.bin"),
                        str(tmp / "pics.bin")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    raw = (tmp / "texts.bin").read_bytes()
    texts, i = [], 0
    while i < len(raw):
        (n,) = struct.unpack_from("<I", raw, i)
        texts.append(raw[i + 4:i + 4 + n])
        i += 4 + n
    got = set(texts)
    for line in (BENCH / "test" / "passages.jsonl").read_text().splitlines():
        p = json.loads(line)
        assert pack.device_text(p["text"]) in got, f"passage {p['id']} did not decode verbatim"
        assert p["citation"].encode("latin-1") in got, f"citation of {p['id']} missing"
    for n in json.loads((BENCH / "test" / "trees.json").read_text())["nodes"]:
        assert n["label"].encode("latin-1") in got, f"label of node {n['id']} missing"

    from PIL import Image
    import numpy as np
    want = b""
    for p in json.loads((BENCH / "test" / "pictures.json").read_text()):
        im = np.array(Image.open(BENCH / "test" / "pictures" / p["file"]).convert("1"))
        want += np.packbits(~im, axis=1).tobytes()     # 1 = ink, rows MSB first
    dumped = (tmp / "pics.bin").read_bytes()
    assert len(dumped) == len(want)
    assert dumped == want, "picture pixels differ from the source PNGs"


def test_voice_commands_land_on_their_nodes(built):
    for c in json.loads((BENCH / "test" / "phrases.json").read_text()):
        r = subprocess.run([str(built["cartcli"]), str(built["lib"]), "cmd", str(c["cmd_id"])],
                           capture_output=True, text=True)
        assert r.returncode == 0 and r.stdout.strip() == str(c["node"]), (c, r.stdout, r.stderr)
    phrases = [l.split("\t") for l in (BENCH / "phrases.txt").read_text().splitlines()]
    assert phrases == [[str(c["cmd_id"]), c["phrase"]]
                       for c in json.loads((BENCH / "test" / "phrases.json").read_text())]


def test_info_and_nodes(built):
    r = subprocess.run([str(built["cartcli"]), str(built["lib"]), "info"], capture_output=True, text=True)
    assert r.returncode == 0 and "9 nodes" in r.stdout, r.stdout
    trees = json.loads((BENCH / "test" / "trees.json").read_text())
    for n in trees["nodes"]:
        r = subprocess.run([str(built["cartcli"]), str(built["lib"]), "node", str(n["id"])],
                           capture_output=True, text=True)
        assert r.returncode == 0 and n["label"] in r.stdout, (n["id"], r.stdout)
