# Voice router + per-tree experts

Training side of the on-device "pseudo mixture of experts" that turns what the recognizer
heard into a card. Design and numbers: [docs/VOICE_ROUTER.md](../../docs/VOICE_ROUTER.md).
C reader for the firmware: [firmware/components/vrouter](../../firmware/components/vrouter).

Nothing here is generative and nothing here ships except the small `.vxp` tables: every
output is the index of a signed-off, cited card (or "say that again").

## Run it (desktop)

```bash
conda activate firstaid
pip install torch pyyaml numpy sentence-transformers   # once
cd pipeline/voice_router
T=~/first-aid-build/pipeline/plb/content/trees              # drafts now, content/trees when signed off

python -m vr router --trees $T                               # out/router.vxp + router.md
python -m vr expert bone-and-joint-injuries --trees $T       # out/expert-<tree>.vxp + .md
python -m vr sweep --trees $T                                # size vs accuracy
python -m vr curve --trees $T                                # accuracy vs amount of seed data

# more training sentences from the local model (Ollama), then retrain with them
python -m vr.generate --trees $T --n 40
python -m vr router --trees $T --extra out/generated --teacher sentence-transformers/all-MiniLM-L6-v2
```

`--teacher` starts each word's vector from a sentence-embedding model (MiniLM is already
on the desktop), squeezed to the router's size. It only changes training; the device
still reads one small row per word.

Check the C reader matches Python exactly:

```bash
cc -O2 -o /tmp/vr_test ../../firmware/components/vrouter/host_test.c ../../firmware/components/vrouter/vrouter.c -lm
/tmp/vr_test out/router.vxp out/router.golden.txt
```

## Data

- `pipeline/plb/content/voice/topics.yaml`: router topics -> tree, seed `train` and
  held-out `test` sentences, plus a `reject` list.
- `pipeline/plb/content/voice/experts/<tree>.yaml`: per-node seed and held-out sentences.
- Lexicon: `pipeline/plb/content/lexicon.yaml` from the speech recognizer thread once it
  exists (ids 0-1023, append-only); until then one is harvested with the same reserved ids.

Keep `test` sentences out of `train` (and out of anything generated): they are the only
honest measure.
