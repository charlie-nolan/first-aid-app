# Run captures

Charlie (2026-11-17): every training run or simulation gets a terminal-style picture of it
working, named `<date>_<what>_run-<run>_test-<n>.png`, with the raw text beside it (`.txt`).
Made with `python -m vr.capture` (pipeline/voice_router/vr/capture.py). Captures hold scores and
the engine's own words only, never manual text (personal content stays off GitHub).

## What each capture shows

| Capture | Setup | Measures |
|---|---|---|
| `2026-11-17_voice-router-tests_run-1396446_test-1` | Unit tests at 1396446 | Code paths pass on small made-up trees, not accuracy |
| `2026-11-17_form-sim-fixture-bites_run-1396446_test-2` | Form sim on a small **made-up** bites tree, typed text | Shows the form working; not a score on the real trees |
| `2026-11-17_form-sim-dev-test2-test3_run-7005be8-v16b2_test-2` | Form sim on the real personal trees, slots v16b2, **typed text, right tree given** | Right card by turn; test2/test3 turn 2 with the form 81% / 78.5% |
| `2026-11-17_rank-asr-dev_asr-eval_run-3d07632-v16b_test-1` | Recognizer 5-best into the form, **right tree given**, TTS clips + 56 of Charlie's bench clips | Right card rank after turn 1 and turn 2 with real decoding |
| `2026-11-20_engine-verify_run-6a9e39a-run17_test-1` | Engine 6a9e39a, slots run16 (frozen) vs run17 (C8 bleeding first), seeds + 157 scenes, typed text | first_sentence changes (0 new WRONG), dev sim, guard 0/122, fuzz, parity case count, spot-line cards |
| `2026-11-20_keyword-search_run-de06aaa_test-1` | vr.search de06aaa, 54 public query words, run17 trees + slots | Hits, life cards and first 8 node ids per word; per-query time |
| `2026-11-20_keyword-search_run-a696cb2_test-1` | vr.search a696cb2 (shared life phrases not indexed), same 54 words | Words whose first 8 changed vs de06aaa; life-card ids per word |
| `2026-11-20_keyword-search_run-b627942_test-1` | vr.search b627942 (own-topic life card first), same 54 words | Changed words, first hit, topic root in top 3 |
| `2026-11-20_keyword-search_run-4ce361e_test-1` | vr.search 4ce361e (whole topic name needed), same 54 words | Changed words, life-card first hits, topic root misses |
| `2026-11-20_keyword-search_run-43e8d08_test-1` | vr.search 43e8d08 (named tree leads), same 54 words | Changed words, first-hit check, topic root check |
| `2026-11-20_keyword-search_run-6244a99_test-1` | vr.search 6244a99 (search-only name "bleeding"), same 54 words | Final check for the firmware handoff: 1 word changed, both checks pass |
| `2026-11-20_keyword-search_run-65ea22a-run18_test-1` | Router 65ea22a (vr.search 77cee50), run18 slots with search_words | Changed words vs 6244a99/run17, leg/arm/dizzy/knee reach, plain-intake 0 changes |
| `2026-11-20_parity-refresh_run-82db807-snap1416_test-1` | Engine 82db807 on frozen snapshot snap_2026-11-20_1416 (run19 in progress) | Slot keys changed vs run18, first_sentence changes (0 new WRONG), guard, parity case counts (files stay local) |
| `2026-11-20_router-size_run-01_test-1` | Neural router sizes (device-exact int4/int8), trained on Ollama lines only; tests gemma TEST3 + Claude scenarios, never trained on | Top-1/top-3, auto-open and wrong auto-open per size vs keyword keep_trees; S3 time is an estimate, not measured |
| `2026-11-20_tab-order_run-d4e0125_test-1` | Multi-injury tabs (TALK+WIN_R) d4e0125, seeds + 157 scenes, run17 | Tabs per line (lead, card, urgency rank), order flags, same-card tabs (39 found) |
| `2026-11-20_tab-order_run-0407c74_test-1` | Tabs 0407c74 (one tab per life card) | Counts, same-card tabs, order, spot lines, multi-scene metric (2/10) |
| `2026-11-20_tab-order_run-9bc2864_test-1` | Tabs 9bc2864 | Same checks; tabs lost vs 0407c74 |
| `2026-11-20_tab-order_run-940f447_test-1` | Tabs 940f447 (integer ranks) | Same checks plus r0/r1 rank and one-tree-two-tabs checks |
| `2026-11-20_tab-order_run-65ea22a_test-1` | Tabs 65ea22a (final for parity_tabs) | All checks clean; multi-scene 3/10 (misses are routing/data) |
| `2026-11-23_keep-cut_run-1c77ac2_test-1` | keep_trees one-third cut 1c77ac2 (on 0412b89) vs 82db807, partial run20 slots (17:47), seeds + 157 scenes | first_sentence changes (4 new WRONG: a lone kept tree auto-opened), sim, guard, tabs, multi-scene 5/10; board turns' kept trees only |
| `2026-11-23_keep-cut_run-da65496_test-1` | Cut keeps at least 2 trees (da65496), same frozen slots | Still 4 new WRONG (doubling put the leader at SURE 0.80); multi-scene 7/10 |
| `2026-11-23_keep-cut_run-a105c95_test-1` | Shares (p) from uncut scores (a105c95), same frozen slots | 0 of 666 first_sentence changes, guard 0/122, multi-scene 7/10; femur board turn keeps fractures + sprains (no Burns) |
