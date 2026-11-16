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
- `fasr/lexicon.py`: builds the shared lexicon (ids 0-1023, append-only, class byte,
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
