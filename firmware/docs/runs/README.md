# Firmware run captures

Named `<date>_<what>_run-<commit>_test-<n>.png`, raw text beside it. Scores and the engine's own words
only, never card text (the real trees are personal).

| Capture | Shows |
|---|---|
| `2026-11-17_vform-c-parity-fixture_run-98d75eb_test-1` | C form engine vs the Python on the made-up fixture trees: 42/42 cases identical, 0 tap loops |
| `2026-11-17_vform-c-parity-real-trees_run-98d75eb_test-2` | Same on the real trees (slots v16b, Diy MoE's parity set): 770/770 cases, 1633 frames identical, 0 tap loops |
| `2026-11-17_vform-bench-build-esp32s3_run-98d75eb_test-3` | The on-board bench app builds with ESP-IDF 5.3.2 for the plain XIAO S3 (not flashed yet: board busy) |
