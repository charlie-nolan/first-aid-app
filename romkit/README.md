# romkit: cartridge ROM packer and reader

Turns the pipeline's intermediate folder into `library.bin`, and reads it back
on the device with a ~4 KB C reader (4.2 KB on Cortex-M4 at -Os). The format is in [FORMAT.md](FORMAT.md).

    python3 pack.py build/intermediate build/library.bin     # also writes build/library.ids.json
    gcc -O2 -o cartcli reader/cart.c reader/cartcli.c
    ./cartcli build/library.bin info
    ./cartcli build/library.bin node 0
    ./cartcli build/library.bin cmd 3          # ESP-SR command 3 -> node id
    ./cartcli build/library.bin contexts       # tag bits
    ./cartcli build/library.bin voice home Washington,winter   # voice list at home in that context
    ./cartcli build/library.bin render 12 1 0 page.pbm   # text 12, medium font, page 0

With the builder:

    plb pack --packer "python3 /path/to/romkit/pack.py"
    plb test --reader-cmd /path/to/cartcli --library build/library.bin

Needs Python 3 with numpy, Pillow, freetype-py and uharfbuzz (`pip install -r requirements.txt`; without uharfbuzz the fonts pack with no kerning). Packing ~150 KB of text takes about 2 s.

Tests (C reader round trip on the bench cartridge, and a check that the committed
`firmware/library/library.bin` is not stale): `python -m pytest -q tests`. CI runs them on every PR.

## Files

- `pack.py`: the packer (intermediate -> ROM).
- `textcodec.py`: Re-Pair + canonical Huffman text codec, with a Python reference decoder.
- `contexts.py`: region / season / activity tags and per-section voice lists.
- `fontpack.py`: bundled fonts, Lexend Deca and Bitter at three sizes (`fonts/`).
- `piccodec.py`: 1-bit picture codecs (raw, PackBits, context model + range coder).
- `reader/cart.h`, `reader/cart.c`: the device reader (C99, no malloc).
- `reader/cartcli.c`: host CLI for tests. `reader/test_cart.c`: round-trip dump test.
- `tests/test_reader.py`: packs the bench cartridge and decodes every text, picture and command through `reader/cart.c`.
- `bench/`: the benchmark scripts and their results (`RESULTS.md`).
