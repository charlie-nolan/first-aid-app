#!/usr/bin/env bash
# Run all the desktop back-work in the background, one step after another, with logs.
#   pipeline/gpu/run_all.sh            start it (SKIP_DRAFTS=1 to leave out the tree drafts) (returns at once; safe to close the terminal)
#   tail -f $WORK/logs/run_all.log     watch it
# Steps: bake the cards -> draft the 15 trees with the local model -> voice clips ->
# ranker experiments. A failed step is logged and the next one still runs.
set -uo pipefail
source "$(dirname "$0")/common.sh"
if [ "${1:-}" != "--now" ]; then
    nohup setsid "$0" --now > "$LOGS/run_all.log" 2>&1 < /dev/null &
    echo "started (pid $!). Watch: tail -f $LOGS/run_all.log"
    echo "Results: $WORK/reports, pipeline/plb/content/drafts/, $WORK/clips"
    exit 0
fi
activate
cd "$(dirname "$0")"
MODEL="${MODEL:-qwen3:30b-a3b-instruct-2507-q4_K_M}"
run() { local name=$1; shift; echo; echo "===== $(date '+%F %T') $name"; "$@" > "$LOGS/$name.log" 2>&1 \
        && echo "ok: $name" || echo "FAILED: $name (see $LOGS/$name.log)"; tail -3 "$LOGS/$name.log"; }

run bake ./bake.sh
if [ "${SKIP_DRAFTS:-0}" = 1 ]; then
    echo "skip draft_trees (SKIP_DRAFTS=1)"
elif command -v ollama >/dev/null; then
    ollama list | grep -q "${MODEL%%:*}" || ollama pull "$MODEL"
    wait_for_gpu 5000
    run draft_trees python draft_trees.py --model "$MODEL"
    [ -f tree_topics.personal.yaml ] && run draft_trees_personal python draft_trees.py --personal --model "$MODEL" --topics tree_topics.personal.yaml
else
    echo "skip draft_trees: ollama not found"
fi
wait_for_gpu 2000
run clips python make_clips.py --out "$WORK/clips" --cuda
run ranker_text python ranker_eval.py text --out "$WORK/reports/ranker-text.md"
run ranker_audio python ranker_eval.py audio --clips "$WORK/clips" --out "$WORK/reports/ranker-audio.md"
echo; echo "===== $(date '+%F %T') all done"
