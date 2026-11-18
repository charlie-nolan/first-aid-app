# Firmware run captures

Named `<date>_<what>_run-<commit>_test-<n>.png`, raw text beside it. Scores and the engine's own words
only, never card text (the real trees are personal).

| Capture | Shows |
|---|---|
| `2026-11-17_vform-c-parity-fixture_run-98d75eb_test-1` | C form engine vs the Python on the made-up fixture trees: 42/42 cases identical, 0 tap loops |
| `2026-11-17_vform-c-parity-real-trees_run-98d75eb_test-2` | Same on the real trees (slots v16b, Diy MoE's parity set): 770/770 cases, 1633 frames identical, 0 tap loops |
| `2026-11-17_vform-bench-build-esp32s3_run-98d75eb_test-3` | The on-board bench app builds with ESP-IDF 5.3.2 for the plain XIAO S3 (not flashed yet: board busy) |
| `2026-11-17_vform-c-parity-real-trees_run-727406e_test-4` | After porting 332d319 + 727406e (negated words never pick a tile): 770/770 identical, 0 tap loops |
| `2026-11-17_vform-c-parity-fixture_run-727406e_test-5` | Fixture with scripted cases (bare yes/no, life threat said mid-form, "not on the face"): 58/58 identical |
| `2026-11-17_tether-board-brain-fake-board_run-727406e_test-6` | Bridge `--board-brain` end to end against a fake board running the C engine: 192/198 frames match the Python shadow (the 6 are a test-harness race); C time per frame on the PC |
| `2026-11-17_vform-c-parity-real-trees_run-04d654f_test-7` | After porting 04d654f (kit branches, implied ties, named finding): real trees 766/766, fixture 73/73, 0 tap loops |
| `2026-11-17_vform-c-parity-real-trees_run-8cafb05_test-8` | Safety port 8cafb05 (walk stops on an in-tree life card): real trees 766/766, 0 tap loops |
