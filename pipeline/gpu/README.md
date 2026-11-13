# Desktop GPU back-work

Scripts for Charlie's desktop (RTX 3070, Manjaro). Everything runs offline after setup,
in its own conda env (`firstaid`), and never touches the cyberdeck venvs or `~/subaru_cyberdeck/kb`.
Big files go to `$WORK` = `/mnt/data/first-aid-gpu` (the home drive is 93% full).

Nothing here puts AI on the device. The device still only shows cited manual passages.
The local model only *drafts* trees for review, and the voice tools only *test* the phrase list.

## Run it

```bash
cd "/home/charlie/first aid project"
git fetch origin && git checkout claude/project-thread-zrhczn && git pull
pipeline/gpu/setup.sh          # once: conda env, plb, 14 offline voices (~1 GB)
pipeline/gpu/run_all.sh        # everything below, in the background
tail -f /mnt/data/first-aid-gpu/logs/run_all.log
```

For the personal (Aerie) tree drafts, put `tree_topics.personal.yaml` (sent in the project
thread; gitignored) into `pipeline/gpu/` before `run_all.sh`.

| Step | Script | GPU | Output |
|---|---|---|---|
| Bake the cards | `bake.sh` | no | `build/library.bin` (+ `library_personal.bin`), verbatim, voice and review reports in `$WORK/reports/<time>/`, and the library copied to `firmware/library/` for the Plus |
| Draft trees from the manuals | `draft_trees.py` | Ollama | `pipeline/plb/content/drafts/<topic>.yaml` + `drafts/review/<topic>.md` (personal: `drafts_personal/`) |
| Voice clips | `make_clips.py` | Piper | `$WORK/clips/*.wav` + `manifest.jsonl` (each test sentence, 6 voices, wind / water / breath noise) |
| Ranker experiments | `ranker_eval.py text`, `audio` | yes | `$WORK/reports/ranker-*.md`: how often a small embedding model, and Whisper on noisy clips, would pick the right card |

`run_all.sh` waits for free GPU memory before each GPU step, so the cyberdeck cron job can keep running.

### Tree drafts

`tree_topics.yaml` lists the 15 wilderness topics from the tree plan and the exact sections
each may cite. The model only picks among those (by number, enforced by a JSON schema), so
it can't invent a source or write body text. It writes the questions, answers (at most 4,
one per corner button) and voice phrases. plb builds every draft with the real rules and
sends any errors back to the model, up to 4 tries. A draft that still fails keeps its errors
in a comment at the top.

To accept a draft: read `drafts/review/<topic>.md`, edit the YAML, move it to
`content/trees/` (or `trees_personal/`), add held-out test sentences in
`content/phrases/synthetic/`, then `plb test` and `plb voice`.

## Plus firmware

```bash
pipeline/gpu/build_plus.sh --flash             # normal build with the baked library
pipeline/gpu/build_plus.sh --bench --flash     # voice bench build (stays awake, listens on serial)
```

## Real ESP-SR scores (needs the Plus + PDM mic + a speaker)

Flash the bench build, put a small speaker 20-50 cm from the board in a quiet room, then:

```bash
conda activate firstaid
python pipeline/gpu/bench_loop.py --clips /mnt/data/first-aid-gpu/clips --limit 400
```

It plays each clip, sends `L <ms>` so the board listens, and reads the `VB` result line.
Report: `clips/bench-<time>.md` (hit / near / miss / wrong / false, by noise and voice).
