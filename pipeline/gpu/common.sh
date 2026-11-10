# Shared settings for the GPU scripts. Sourced, not run.
# Everything heavy lives under $WORK (default /mnt/data/first-aid-gpu: the home drive is 93% full).
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PLB="$REPO/pipeline/plb"
if [ -z "${WORK:-}" ]; then
    if [ -d /mnt/data ] && [ -w /mnt/data ]; then WORK=/mnt/data/first-aid-gpu; else WORK="$HOME/first-aid-gpu"; fi
fi
ENV_NAME="${ENV_NAME:-firstaid}"
LOGS="$WORK/logs"
mkdir -p "$WORK" "$LOGS"

activate() {
    # conda env made by setup.sh (Python 3.11: piper and onnxruntime have no 3.14 wheels yet)
    local base; base="$(conda info --base 2>/dev/null || echo "$HOME/miniconda3")"
    # shellcheck disable=SC1091
    source "$base/etc/profile.d/conda.sh"
    conda activate "$ENV_NAME"
}

# Wait until the GPU has at least $1 MiB free (the cyberdeck cron job can hold ~6.8 GB).
wait_for_gpu() {
    local need="${1:-2500}" free
    command -v nvidia-smi >/dev/null || return 0
    while :; do
        free=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -1)
        [ "${free:-0}" -ge "$need" ] && return 0
        echo "$(date +%T) GPU has ${free} MiB free, waiting for ${need} MiB..."
        sleep 60
    done
}
