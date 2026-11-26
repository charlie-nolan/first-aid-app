# Custom first-aid speech recognizer (`fasr`)

A small speech model of our own, to run on the XIAO ESP32-S3 Plus in place of ESP-SR MultiNet
(a closed binary). It is trained only for what this device needs: first-aid words.
MultiNet stays in the firmware as the fallback until this one wins on the bench.

How it works, in one line: **audio -> 41 speech sounds (phonemes) -> only words in the lexicon -> word ids -> voice router**.

- `fasr/model.py`: causal 1-D conv net, CTC over 39 ARPAbet phones + word boundary + blank.
  Size `s` = 0.53 M weights (~0.55 MB int8), ~25 M multiply-adds per second of audio, streams.
- `fasr/decode.py`: beam search that may only spell words from
  [`pipeline/plb/content/lexicon.yaml`](../plb/content/lexicon.yaml); anything else becomes
  `<unk>` and raises the reject score. Output = the router's `voice_hyp_t` (5-best word ids + 0-100 confidences + reject).
  New words need only a lexicon line, no retraining.
- `fasr/lexicon.py`: builds the shared lexicon (ids 0-2047, append-only, class byte,
  sound spellings from CMUdict / hand list in `speech_core.yaml` / espeak-ng guess).
- Data: LibriSpeech for general English sounds (pretrain), then Piper TTS voices speaking
  first-aid sentences (fine-tune), Charlie's own recordings, wind/water/breath/echo/mic
  augmentation on the fly. Held-out sentences AND held-out speakers are never trained on.
- `fasr/whisper_lora.py`: Charlie's LoRA idea. Whisper-small + LoRA on the PC (never on the
  device), used as a teacher to label unscripted recordings into extra training data, and as
  a strong reference score.

## Run (desktop, conda env `firstaid` from `pipeline/gpu/setup.sh`)

```bash
nohup bash pipeline/speech/run.sh > $WORK/logs/speech.log 2>&1 &     # everything, resumable
python -m fasr record --mode prompted --minutes 20                     # (cd pipeline/speech) your voice
python -m fasr record --mode eval --minutes 5                          # held-out, scoring only
python -m fasr record --mode free --minutes 10                         # Whisper labels these
```

Steps: `setup lexicon opendata synth pretrain whisper finetune evaluate export`
(`bash run.sh finetune evaluate` reruns two; delete `$WORK/speech/.done/<step>` to redo one).

Sharing the GPU: `touch $WORK/PAUSE_GPU` makes training save, free the GPU and wait; `rm` it
to continue. SIGUSR1/SIGUSR2 do the same; SIGTERM saves and exits; rerunning resumes.

## Judging it

`fasr evaluate` scores held-out sentences x held-out voices x 5 noise conditions (word error,
exact-in-5-best, reject on off-topic) and writes the n-best per clip for the router thread.
The real verdict is `pipeline/gpu/bench_loop.py` on the Plus, same clips, against MultiNet.

## Stage 3: shared trunk + expert tails (`fasr tail`)

The device runs the trunk (stem + first blocks, frozen) once and keeps its int8 output H. When
the router picks a group, it loads that group's tail (last 4 blocks + head, ~0.29 MB for m) and
re-runs only the tail on H. Split: m = blocks 0-9 | 10-13, l = 0-11 | 12-15. Groups and their
words: `pipeline/plb/content/voice/tail_groups.yaml` (life-word clips train every tail).
`bash run.sh tails` trains one tail per group, scores each against the base (keyword recall,
wrong keywords per clip, life words missed) and exports the kept ones as EXPT files with goldens
to `$WORK/speech/export/tails/<base export>/<group>/`. File format: `fasr/tail.py` docstring.

## Recording through the device's own mic (best training data)

`firmware/recorder` is a separate small firmware. It shows a sentence on the e-ink. You hold TALK, read the sentence, and let go. The clip then goes over USB to the PC.

- **Plain bench board wiring:** unplug the LED gate jumpers from D4 and D5. Wire the PDM mic as CLK to D4, DAT to D5, 3V to 3V3 and GND to GND, with SEL tied to GND. The library (fonts) must already be in flash, so flash the main firmware once first.
- **Build and flash:** `cd firmware/recorder && idf.py build flash`. For the Plus, use `idf.py -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.plus" build flash`.
- **On the PC:** run `python -m fasr devrec`. Leave it running and clips are saved as you go to `$WORK/speech/me_dev/`. Anything not yet copied stays in PSRAM (about 3 minutes' worth) until you plug in.
- **Buttons:** TALK hold = record. BACK = redo the last clip. DOWN = skip, and holding DOWN switches mode. The modes are TRAIN, EVAL (held-out), FREE (Whisper labels it later) and NOISE (10 s of background, used as real noise in training).
- **Prompt list:** `python -m fasr devrec prompts` regenerates `firmware/recorder/main/prompts.txt`. It holds 400 training and 120 held-out sentences.
