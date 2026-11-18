# vform: the fill-in-the-blanks form, in C

C port of `pipeline/voice_router/vr/intake.py` + `vr/form.py` (+ `vr.live.keep_trees`), from
`claude/project-thread-ypggtj` at **727406e**. The Python stays the reference: every behaviour change
there is ported here and re-checked with the parity set below.

What it does: the first sentence fills every blank it can in the 2-3 likely trees; one screen lists
the blanks still open (a "Which is it?" field while trees compete); each answer, spoken or tapped,
fills its field; when the blanks lead to one card, that signed-off, cited card opens. Nothing is
generated. The safety rules are the Python's, ported line for line (heard words only, negation and
hedges fill nothing, lower guesses only add life answers, snap never on a life blank, life cards open
only when a life rule fires).

| File | What |
|---|---|
| `vform.h/.c` | engine: tokenize, stem, fill, fill_nbest, walk, answer_turn, snap, keep_trees, first_sentence, open_fields, answer, pick, ranked_cards, most_telling, split_tabs |
| `vform_show.h/.c` | the tether's SHOW JSON frames, same bytes as `pc_bridge.py` / `vr/parity.py` |
| `tools/formpack.py` | trees + slot maps -> one `FPK1` blob, read in place from flash |
| `tools/parity_check.py` | replays a `vr.parity` set through the C, compares every frame byte for byte |
| `tools/make_fixture.py` | made-up fixture trees -> `test/fixture.fpk` + `test/fixture_parity.jsonl` (safe to commit) |
| `host_test.c` | PC runner for the check (and a C tap-loop fuzz, like `vr.form.fuzz`) |
| `../../vform_bench` | the same replay on the board, with per-frame timing |

## Check it (PC)

```bash
cd firmware/components/vform
cc -O2 -ffp-contract=off -o /tmp/vform_test host_test.c vform.c vform_show.c -lm
python tools/parity_check.py --bin /tmp/vform_test --pack test/fixture.fpk --parity test/fixture_parity.jsonl --fuzz
```

Real trees (personal: the pack and the parity set stay on the PC). Tree order matters, first hit wins:

```bash
VR=<pipeline/voice_router at 98d75eb or later>
python tools/formpack.py --vr $VR --trees ~/first-aid-trees/pipeline/plb/content/trees_personal \
    ~/first-aid-build/pipeline/plb/content/trees --slots ~/first-aid-voice/slots -o /tmp/real.fpk
python tools/parity_check.py --bin /tmp/vform_test --pack /tmp/real.fpk \
    --parity /mnt/data/first-aid-gpu/speech/me_dev/parity/parity_98d75eb_v16b.jsonl --fuzz
```

Results so far (captures in `firmware/docs/runs/`): fixture 42/42 cases identical; real trees
770/770 cases, 1633 frames identical; fuzz 0 tap loops. The C replays the 770 cases in about 4 s on
the PC, where the Python takes about 40 s.

## Pack format

`FPK1`, layout in the `tools/formpack.py` docstring. All strings are interned once; cards, tiles and
slot words are small fixed records with u16 string ids. Tiles and questions are computed by the
Python reference itself (`vr.intake.tiles`, `_questions`), so the C never redoes the slot-map/inline
merge. 32 real trees = 141 KB. This is the per-tree "blanks and word lists" part of `library.bin`;
moving it into the cartridge is the next step (romkit section).

## Known gaps

- Sentences longer than 40 words or words longer than 27 letters are cut (the Python has no limit).
- Strings are compared as ASCII; non-ASCII letters act as spaces, like the Python's tokenizer regex.
- `vr.intake.life_options` (two life tiles on a sound-alike) is not ported yet; `vr.form` does not use it.
- Ranking work space: about 240 KB of heap (PSRAM on the board).
