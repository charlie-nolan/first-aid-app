# asr: the speech engines

One interface (`asr.h`) for three engines, picked in menuconfig ("Speech recognizer engine"):

| Engine | State |
|---|---|
| **DIY int8** (default) | `fasr_frontend.c` (log-mel) -> `fasr_q8.c` (int8 conv model) -> `fasr_decode.c` (word-list beam search, fit_words, calibrated reject). Ported from `pipeline/speech/fasr` (claude/project-thread-vjwm7d, export spec f3f785b). |
| ESP-DL | placeholder (`ASR_NOT_BUILT`) |
| MultiNet | the fallback, still in `main/voice.c` |

The model, lexicon + trie and reject calibration ride in the cart as chunks `ASRM`, `LEXI`, `ASRC`
(`firmware/tools/cart_add.py`), so swapping a model is a cart rebuild, not a firmware change.

## Pack an export into the cart

```bash
X=$WORK/speech/export/<commit>           # from `python -m fasr export_int8 --run firstaid`
F=<pipeline/speech at that commit>
python tools/asrpack.py $X -o asrm.bin
python tools/lexpack.py --fasr $F --lexicon $X/lexicon.yaml -o lexi.bin
python tools/calibpack.py $X/reject_calib.json asrc.bin
python ../../tools/cart_add.py library_personal.bin -o library_voice.bin ASRM=asrm.bin LEXI=lexi.bin ASRC=asrc.bin FORM=form.fpk
```

## Checks (PC)

| Test | Against | Result so far |
|---|---|---|
| `host_test.c` (model) | `golden/NN.npz` from the export (every layer's int8 output, logits, log-probs) | bit-exact on a dummy xs export |
| `decode_test.c` (search) | `fasr.decode.decode()` on synthetic phone posteriors (16 cases incl. a 26-word sentence and noise) | 16/16 identical (ids, confidences, raw + calibrated reject, veto, untrimmed length) |
| `fe_test.c` (front end) | `fasr.features.logmel` | within 1 float32 ulp (numpy's FFT/matmul order differs) |

Real golden files land with the real export; run the same three tests on them first.
