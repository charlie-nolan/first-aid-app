# Run captures

Charlie (2026-11-17): every training run or simulation gets a terminal-style picture of it
working, named `<date>_<what>_run-<run>_test-<n>.png`, with the raw text beside it (`.txt`).
Made with `python -m vr.capture` (pipeline/voice_router/vr/capture.py). Captures hold scores and
the engine's own words only, never manual text (personal content stays off GitHub).
