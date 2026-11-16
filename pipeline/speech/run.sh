#!/usr/bin/env bash
# Custom first-aid recognizer, end to end on the desktop GPU. Every step is resumable:
# run it again and finished steps are skipped (delete a step's marker in $WORK/speech/.done to redo it).
#   bash pipeline/speech/run.sh            # all steps
#   bash pipeline/speech/run.sh synth      # one step: setup lexicon opendata synth pretrain whisper finetune evaluate export
# Long steps: run detached, e.g.  nohup bash pipeline/speech/run.sh > $WORK/logs/speech.log 2>&1 &
# Pause for the cyberdeck: touch $WORK/PAUSE_GPU (training saves and frees the GPU), rm it to resume.
set -euo pipefail
source "$(dirname "$0")/../gpu/common.sh"
activate
SP="$WORK/speech"; mkdir -p "$SP/.done"
cd "$REPO/pipeline/speech"
export WORK PYTHONUNBUFFERED=1
TREES="${TREES:-$HOME/first-aid-build/pipeline/plb/content/trees}"
PERSONAL="${PERSONAL:-$HOME/first-aid-fw/pipeline/plb/content/trees_personal}"
GEN="${GEN:-$REPO/pipeline/voice_router/out/generated}"
tree_args() { for d in "$REPO/pipeline/plb/content/trees" "$TREES" "$PERSONAL"; do [ -d "$d" ] && printf '%s ' "$d"; done; }
extra_args() { [ -d "$GEN" ] && printf '%s' "$GEN"; }

step() { local name=$1; shift; if [ -e "$SP/.done/$name" ]; then echo "== $name: done already"; return; fi
         echo "== $name: $(date)"; "$@"; touch "$SP/.done/$name"; echo "== $name finished $(date)"; }

s_setup()    { pip install -q cmudict soundfile sounddevice scipy peft "transformers>=4.44" accelerate onnx
               python -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"; }
s_lexicon()  { python -m fasr lexicon --trees $(tree_args) --extra $(extra_args); }   # append-only
s_opendata() { python -m fasr opendata --out "$SP/librispeech"; }
s_synth()    { python -m fasr synth --out "$SP/tts" --voices "$WORK/voices" --trees $(tree_args) --extra $(extra_args) --cuda; }
s_pretrain() { wait_for_gpu 3000; python -m fasr train --run pretrain --size "${SIZE:-s}" --hours "${PRE_HOURS:-6}" \
                 --train "$SP/librispeech/train.jsonl" --dev "$SP/librispeech/dev.jsonl"; }
me_args()    { [ -s "$SP/me/train.jsonl" ] && printf -- '--train %s:3 ' "$SP/me/train.jsonl"
               [ -s "$SP/me/labelled.jsonl" ] && printf -- '--train %s:2 ' "$SP/me/labelled.jsonl"; true; }
s_whisper()  { wait_for_gpu 6500; python -m fasr whisper train --train "$SP/tts/train.jsonl" $(me_args) \
                 --dev "$SP/tts/dev.jsonl" --hours "${WHISPER_HOURS:-2}"
               if [ -d "$SP/me/free" ]; then python -m fasr whisper label --audio "$SP/me/free" --out "$SP/me/labelled.jsonl"; fi
               python -m fasr whisper score --set "$SP/tts/eval.jsonl" | tee "$SP/whisper-score.txt"; }
s_finetune() { wait_for_gpu 3000; python -m fasr train --run firstaid --init pretrain --hours "${FT_HOURS:-3}" --lr 1e-3 \
                 --train "$SP/tts/train.jsonl:6" --train "$SP/librispeech/train.jsonl:1" $(me_args) --dev "$SP/tts/dev.jsonl"; }
s_evaluate() { sets="--set $SP/tts/eval.jsonl"; [ -s "$SP/me/eval.jsonl" ] && sets="$sets --set $SP/me/eval.jsonl"
               python -m fasr evaluate --run firstaid $sets | tee "$SP/eval-firstaid.txt"; }
s_export()   { python -m fasr export --run firstaid; }

ALL="setup lexicon opendata synth pretrain whisper finetune evaluate export"
for s in ${@:-$ALL}; do step "$s" "s_$s"; done
