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
