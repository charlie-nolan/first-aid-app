# Voice router + per-tree experts (pseudo mixture of experts)

Status: prototype, 2026-11-16. Trained on the 11-10 draft trees (unreviewed) and on seed
sentences written by Claude for Charlie's review. Nothing here is signed off.

## What it is

Three stages, each small and testable on its own:

```
 mic -> recognizer ------------> router --------------> expert (one per tree) ----> card
        (speech thread)          always mapped           mapped when its tree opens
        sound -> word ids        words -> topic          words + current card -> card
        n-best + confidence      or "reject"             or "none" (= back to router)
```

- **Recognizer** (thread "Build custom first-aid speech recognizer"): sound to word ids from the
  cartridge lexicon. Until it exists, MultiNet stands in: each recognized phrase is split
  into lexicon ids with MultiNet's probability as the confidence.
- **Router**: always loaded. Picks the topic (34 now: fracture, cold, burn, snake bite...) or
  "reject" ("say that again").
- **Expert**: one per tree. Picks a card inside its tree, so a user can answer the screen in
  their own words or jump ahead ("the bone is sticking out", "I don't have a splint"). It knows
  which card is on screen. If the words belong to another topic it answers "none" and the
  router takes over.

Hard rule kept: no generative model. Every output is the index of a signed-off, cited card.
Buttons stay primary; numbers 1-4 keep working inside trees (Charlie 11-13).

## Interface with the recognizer (agreed 2026-11-16)

```c
typedef struct {
    uint8_t n;                                   /* up to 5 guesses, best first */
    struct { uint8_t len; uint16_t id[16]; uint8_t conf[16]; } h[5];
    uint8_t reject;                              /* 0-100: "nothing usable" */
} voice_hyp_t;
```

Lexicon ids 0-1023, append-only (0 UNK, 1-4 one..four, 5 yes, 6 no, 7 back, 8 again, 9 home),
plus a 1-byte class per word (0 other, 1 body part, 2 mechanism, 3 symptom, 4 negation,
5 quantity). The recognizer thread owns `pipeline/plb/content/lexicon.yaml`.

Why words and not an intent code from the recognizer: new trees only need new router rows,
not a new speech model; negation and body-part words reach the expert intact; and the two
halves can be bench-tested separately. A v2 option is an extra intent head on the
recognizer that votes alongside the router.

## The model (same shape for router and experts)

1. Each heard word -> one row of numbers (its meaning), weighted by the recognizer's confidence
   and by which guess it came from (guess 2 counts 0.6x, guess 3 0.36x...).
2. Word pairs ("can't move", "isn't broken") and "word after a negation" -> hashed rows.
3. Expert only: the current card -> one row (so "it's his arm" means sling on the no-splint card).
4. Average 1 and 2, add 3 -> one small layer (ReLU) -> a score per class -> probabilities.

Training-only tricks that cost the device nothing:
- Character trigrams per word (fastText style), folded into each word's row at export: "sliced",
  "slice" and "slicing" share meaning even when only one was in training.
- `--teacher`: start word rows from MiniLM (already on the desktop), squeezed by PCA. Words never
  seen in seed data land near their meaning.
- Fake recognizer noise in every epoch: dropped words, sound-alike swaps, extra filler words,
  up to 4 guesses. Spelling similarity stands in for sound similarity until the lexicon has
  phonemes.

### On the device

- Format `VXP1`: int8 rows with one float scale per row, float biases, class map, names.
  Reader: `firmware/components/vrouter` (plain C, ~150 lines, no allocation).
- A sentence touches only its own rows (a dozen to a few dozen), so models stay in
  memory-mapped flash (`esp_partition_mmap`) and are read in place. "Loading" an expert is
  a pointer swap: no copy into PSRAM, no delay. If flash-cache misses ever show up on the
  bench, copying one expert into PSRAM is ~350 KB at ~40 MB/s, about 10 ms.
- Compute per utterance: ~30 rows x 64 + 64x128 + 128x35, about 15k multiply-adds: well under
  a millisecond on the S3.
- The C reader matches the Python int8 reference to 1e-7 (host test, 12 cases each).

### Flash budget (16 MB Plus)

| item | size |
|---|---|
| router (d=64) | ~350 KB |
| experts, ~20 first-aid trees x ~350 KB | ~7 MB at most; ~2 MB at d=32 (110 KB each) |
| recognizer (speech thread) | 1-3 MB |

Size sweep below says ~110 KB per model is enough today, so plan for d=32 experts and only grow
a tree's expert when its data does. All of this fits next to ESP-SR's front end, firmware
(~1.5 MB) and the library (2-8 MB) if MultiNet's ~4 MB model is dropped once ours wins; while
both run side by side, keep experts at d=32.

## How it plugs into voice.c / ui.c

1. `voice.c` `post()`: instead of mapping MultiNet's id straight to a cartridge command, build a
   `voice_hyp_t` (MultiNet now, our recognizer later) and post it to the UI task.
2. UI task: on a number word (ids 1-4) or back/again/home, act as today. Otherwise:
   - At Home: `vr_score(router)` -> if confident, open that topic's tree and point the expert at
     it; if unsure, show the 3 best topics as tiles (existing picker); if "reject", "say again".
   - In a tree: `vr_score(expert, hyp, current_node)` -> same accept / pick-of-3 / say-again
     rule; "none" -> run the router and switch tree.
3. Thresholds (tunable from bench logs): accept if top probability >= 0.55 and it leads the
   second by >= 0.2; "say again" below 0.2; otherwise pick-of-3.
4. Models live in a `voice` data partition written by the pipeline next to library.bin; the
   cartridge's tree table names each tree's expert offset.

Not wired into the build yet: the component compiles on its own and on the PC. Wiring it waits
for the Plus bring-up branch (claude/project-thread-5qvx6w) and for a partition decision.

## Results (prototype, CPU, 2026-11-16)

Held-out sentences only (never trained on). Noise = fake recognizer error rate. "Auto,
WRONG" is the number to keep low: the device would open the wrong card without asking.
Baseline = nearest seed sentence by shared words, roughly what fixed phrases do when the exact
phrase isn't in the list.

Router (34 topics + reject, 230 seed sentences, 141 held out, d=64, 354 KB):

| noise | top-1 | top-3 | auto, right | auto, WRONG | pick-of-3 has it | say again | baseline |
|---|---|---|---|---|---|---|---|
| 0.00 | 62% | 79% | 24% | 4% | 39% | 21% | 49% |
| 0.15 | 61% | 78% | 22% | 3% | 40% | 23% | 44% |
| 0.30 | 58% | 76% | 20% | 4% | 40% | 24% | 41% |

Fractures expert (23 cards + none, 289 seed sentences, 171 held out, 351 KB):

| situation | top-1 | top-3 | auto, WRONG | baseline |
|---|---|---|---|---|
| answering the card on screen | 76% | 89% | 5% | 50% |
| jumping ahead from the first card | 66% | 82% | 5% | 50% |
| words from another topic (should hand back) | 81% | 95% | 0% | 76% |

Size vs data (router):

| d | KB | top-1 clean | top-1 @0.30 | top-3 @0.30 |
|---|---|---|---|---|
| 16 | 41 | 56% | 48% | 67% |
| 32 | 111 | 57% | 52% | 72% |
| 64 | 350 | 55% | 57% | 74% |
| 96 | 921 | 62% | 57% | 78% |
| 128 | 2280 | 61% | 58% | 79% |

| seed sentences | top-1 clean | top-1 @0.30 | top-3 @0.30 |
|---|---|---|---|
| 58 (25%) | 44% | 38% | 57% |
| 110 (50%) | 47% | 48% | 67% |
| 172 (75%) | 60% | 54% | 74% |
| 230 (100%) | 59% | 57% | 78% |

Reading: past ~100 KB, size buys little; data buys a lot. The test set is small (141), so
differences under ~5 points are noise.

## Next

1. Desktop: `python -m vr.generate` (Ollama, a few thousand sentences), `--teacher` MiniLM,
   retrain; Charlie skims a sample of generated sentences per topic.
2. Experts for the other first-aid trees once they are signed off (one seed file each; the
   generator fills the rest).
3. Swap the harvested lexicon for the recognizer thread's `lexicon.yaml`; sound-alike noise from
   its phonemes; then bench numbers from real clips through `bench_loop.py`.
4. Wire into voice.c/ui.c behind a Kconfig flag on the Plus branch.
5. Context tags (season, region, activity) as extra router rows once there is data for them.
