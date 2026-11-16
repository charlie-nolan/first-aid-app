#!/usr/bin/env bash
# Lets the cyberdeck's GPU lease pause our training, without touching the cyberdeck.
# The lease (~/subaru_cyberdeck/tools/gpu_lease.py, user unit gpu-lease.service) only stops its own
# background job (SIGTERM to the pid in its logs/community_run_9b.pid) and unloads Ollama :11435;
# it knows nothing about us. This loop asks it `status` every 20 s (read-only) and mirrors the answer
# into $WORK/PAUSE_GPU: deck in use -> touch it (fasr train / whisper save and free the GPU),
# idle -> remove it. It only removes a pause file it made itself, so a manual `touch` still holds.
#   nohup bash pipeline/gpu/lease_bridge.sh [PID_TO_WATCH] > $WORK/logs/lease_bridge.log 2>&1 &
# Exits (removing its own pause file) when PID_TO_WATCH ends, if one is given.
set -uo pipefail
source "$(dirname "$0")/common.sh"
LEASE="${LEASE:-$HOME/subaru_cyberdeck/tools/gpu_lease.py}"
LPY="${LPY:-$HOME/subaru_cyberdeck/venv/bin/python}"
PAUSE="${FASR_PAUSE_FILE:-$WORK/PAUSE_GPU}"
MINE="$WORK/.pause_by_lease"
WATCH="${1:-}"

[ -f "$LEASE" ] && [ -x "$LPY" ] || { echo "no cyberdeck gpu lease at $LEASE; nothing to bridge"; exit 0; }
cleanup() { [ -e "$MINE" ] && rm -f "$PAUSE" "$MINE"; }
trap cleanup EXIT
trap 'exit 0' TERM INT
echo "$(date +%T) bridging $LEASE status -> $PAUSE${WATCH:+ while pid $WATCH runs}"
while :; do
    [ -n "$WATCH" ] && ! kill -0 "$WATCH" 2>/dev/null && { echo "$(date +%T) pid $WATCH ended"; exit 0; }
    st=$("$LPY" "$LEASE" status 2>/dev/null | head -1)
    if [[ "$st" == "IN USE"* ]]; then
        if [ ! -e "$PAUSE" ]; then touch "$PAUSE" "$MINE"; echo "$(date +%T) pause: $st"; fi
    elif [ -n "$st" ] && [ -e "$MINE" ]; then
        rm -f "$PAUSE" "$MINE"; echo "$(date +%T) resume: $st"
    fi
    sleep 20
done
