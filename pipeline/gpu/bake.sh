#!/usr/bin/env bash
# Bake the cards: build, every check, pack library.bin (public, then personal if you have
# sources.personal.yaml), verbatim check against the PDFs, voice report, review pages.
# Then copy the library the Plus should carry into firmware/library/.
# CPU only, a few minutes. Reports land in $WORK/reports.
# Optional:
#   CARTCLI=path/to/cartcli        read every packed library back through the C reader
#   VOICE_SLOTS=dir                cross-check the voice router's slot files (<tree>.yaml)
#   SLOTS_MAP=file.md              also copy the personal slot map there
#   VR_DIR=checkout  SEEDS=dir     voice intake sim per tree (VR_DIR has pipeline/voice_router/vr/intake.py,
#                                  SEEDS the expert seed YAMLs; needs VOICE_SLOTS)
set -uo pipefail
source "$(dirname "$0")/common.sh"; activate
cd "$PLB"
# Always this checkout's plb (the conda env's `plb` entry point may be an editable install of another one).
plb() { PYTHONPATH="$PLB${PYTHONPATH:+:$PYTHONPATH}" python -m plb "$@"; }
R="$WORK/reports/$(date +%Y%m%d-%H%M)"; mkdir -p "$R"
PACK="python3 $REPO/romkit/pack.py"
fail=0
step() { echo; echo "== $*"; "$@" 2>&1 | tee -a "$R/bake.log"; local rc=${PIPESTATUS[0]}; [ "$rc" = 0 ] || { echo "!! failed ($rc): $*"; fail=1; }; return 0; }
# a report only: written and logged, never fails the bake
report() { echo; echo "== (report) $*"; "$@" 2>&1 | tee -a "$R/bake.log"; local rc=${PIPESTATUS[0]}; [ "$rc" = 0 ] || echo "   (report step exited $rc; not a bake failure)"; return 0; }

step plb validate
step plb slots --out "$R/slots.md"
step plb pack --packer "$PACK"
cp build/library.bin "$R/library.bin" 2>/dev/null
step plb verbatim --cards --out "$R/verbatim.md"   # reviewed public cards; ORDER does not fail
# old phrase-list voice test, superseded by slot intake (vr.intake sim below): report only
report plb voice --out "$R/voice.md"
step plb review --out "$R/review"
[ -n "${CARTCLI:-}" ] && step plb test --reader-cmd "$CARTCLI" --library build/library.bin
if [ -f sources.personal.yaml ]; then
    step plb --personal slots --out "$R/slots-personal.md" ${VOICE_SLOTS:+--voice-slots "$VOICE_SLOTS"}
    [ -n "${SLOTS_MAP:-}" ] && cp "$R/slots-personal.md" "$SLOTS_MAP"
    step plb --personal pack --packer "$PACK"
    # --all: personal trees are drafts (unreviewed), which plain `verbatim` skips.
    # --cards: tree cards only; a card word missing from the cited pages fails, reading-order
    # differences are listed as ORDER. The reference shelf is reported separately, not failed.
    step plb --personal verbatim --all --cards --out "$R/verbatim-personal.md"
    report plb --personal verbatim --all --out "$R/verbatim-personal-shelf.md"
    report plb --personal voice --out "$R/voice-personal.md"
    [ -n "${CARTCLI:-}" ] && step plb --personal test --reader-cmd "$CARTCLI" --library build/library_personal.bin
    # Voice router pinned to claude/project-thread-ypggtj dce3a3a (PR #21: walk stops on in-tree
    # life cards; f844f7a kept trees read mid-form words first; 5764998 named trees rank first; e310149 emergencies checked per tree in rank order; 1623cdb condition-name topics; 38b1139 plain "not breathing" ranks CPR; fd599af named CO keeps its own rule, heat stroke is not stroke; 47c1afd lower-ranked plain walks open no card; 04df032 trees agreeing on one card open it; bf8354f every kept tree must agree; 31298ea shared CPR rule is not own handling; dce3a3a shared life phrases are not own problem) until PR #21 merges. VR_PIN= (empty) skips the check.
    VR_PIN="${VR_PIN-dce3a3a}"
    if [ -n "${VR_DIR:-}" ] && [ -n "$VR_PIN" ] && \
       [ "$(git -C "$VR_DIR" rev-parse --short=7 HEAD 2>/dev/null)" != "$VR_PIN" ]; then
        echo "!! failed: VR_DIR $VR_DIR is at $(git -C "$VR_DIR" rev-parse --short=7 HEAD 2>/dev/null), bake pins $VR_PIN" | tee -a "$R/bake.log"
        fail=1; VR_DIR=""
    fi
    if [ -n "${VR_DIR:-}" ] && [ -n "${SEEDS:-}" ] && [ -n "${VOICE_SLOTS:-}" ]; then
        echo "== voice sims, router $(git -C "$VR_DIR" rev-parse --short=7 HEAD)" | tee -a "$R/voice-sim.txt"
        for sf in "$VOICE_SLOTS"/*.yaml; do
            t=$(basename "$sf" .yaml); seed="$SEEDS/$t.yaml"
            [ -f "$seed" ] || continue
            echo "== voice sim $t" | tee -a "$R/voice-sim.txt"
            ( cd "$VR_DIR/pipeline/voice_router" && python -m vr.intake "$t" --sim "$seed" \
                --trees "$PLB/content/trees" "$PLB/content/trees_personal" --slots "$sf" ) \
                2>&1 | tee -a "$R/voice-sim.txt"
        done
    fi
fi

# The tracked firmware/library/library.bin is the public bench cart and is never overwritten here.
# The personal library (local only, never committed) goes to the gitignored library_personal.bin;
# build_plus.sh flashes that one when it exists.
cp build/intermediate/phrases.txt "$REPO/firmware/library/phrases.txt" 2>/dev/null || true
if [ -s build/library_personal.bin ]; then
    cp build/library_personal.bin "$REPO/firmware/library/library_personal.bin"
    echo; echo "firmware/library/library_personal.bin <- build/library_personal.bin ($(stat -c %s build/library_personal.bin) bytes)"
    git -C "$REPO" check-ignore -q firmware/library/library_personal.bin \
        || { echo "!! firmware/library/library_personal.bin is NOT gitignored"; fail=1; }
else
    echo; echo "no personal library: the Plus flashes the public firmware/library/library.bin"
fi
echo "reports: $R"
[ "$fail" = 0 ] && echo "BAKE OK" || echo "BAKE HAD FAILURES: read $R/bake.log"
exit $fail
