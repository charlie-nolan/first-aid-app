# Run captures

One terminal-style PNG per training run, simulation or test (Charlie's rule, 2026-11-17), so each PR
shows the run working. Names: `<date>_<what>_<run>_test<NN>.png`.

- Automatic for `python -m fasr train|evaluate|pagescore|slot|calibrate|adapt|export` (tail of output).
- Anything else (router sims, bench logs): `python -m fasr snap --kind <what> --run <run> --log <file>`
  or `python -m fasr snap --kind <what> --run <run> -- <command>`.
- Metrics and progress only: no manual text, prompt lists or transcripts of recordings.
