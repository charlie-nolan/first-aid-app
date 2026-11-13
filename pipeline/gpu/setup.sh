#!/usr/bin/env bash
# One-time setup: a separate conda env "firstaid" (never touches the cyberdeck venvs or kb/),
# the plb builder, and offline voices for test clips. Safe to run again.
set -euo pipefail
source "$(dirname "$0")/common.sh"
base="$(conda info --base)"; source "$base/etc/profile.d/conda.sh"
conda env list | grep -q "^$ENV_NAME " || conda create -y -n "$ENV_NAME" python=3.11
conda activate "$ENV_NAME"

pip install -U pip
pip install -e "$PLB[test]"
pip install numpy Pillow freetype-py uharfbuzz pyyaml   # romkit packer
pip install piper-tts onnxruntime-gpu soundfile scipy   # voices + noise
pip install sentence-transformers faster-whisper        # ranker experiment (uses the HF cache)
pip install pyserial sounddevice requests               # bench loop, Ollama client

# Offline voices (rhasspy/piper-voices). libritts_r has 904 speakers, l2arctic 24 accented
# speakers: lots of different people for the stress test.
VOICES="$WORK/voices"; mkdir -p "$VOICES"
HF=https://huggingface.co/rhasspy/piper-voices/resolve/main/en
for v in en_US/libritts_r/medium en_US/l2arctic/medium en_US/arctic/medium en_US/lessac/medium \
         en_US/amy/medium en_US/ryan/medium en_US/joe/medium en_US/kristin/medium \
         en_US/hfc_female/medium en_US/hfc_male/medium en_GB/alan/medium en_GB/vctk/medium \
         en_GB/northern_english_male/medium en_GB/southern_english_female/low; do
    lang="${v%%/*}"; rest="${v#*/}"; name="${rest%%/*}"; q="${rest##*/}"
    f="$lang-$name-$q.onnx"
    [ -s "$VOICES/$f" ] || { curl -fsSL -o "$VOICES/$f" "$HF/$v/$f" && curl -fsSL -o "$VOICES/$f.json" "$HF/$v/$f.json" \
        && echo "voice $f" || { echo "skip $f (download failed)"; rm -f "$VOICES/$f" "$VOICES/$f.json"; }; }
done
python - <<'PY'
import onnxruntime as o; print("onnxruntime providers:", o.get_available_providers())
PY
echo "setup done. Work folder: $WORK"
