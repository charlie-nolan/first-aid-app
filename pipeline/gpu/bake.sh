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
step plb verbatim --out "$R/verbatim.md"
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
    if [ -n "${VR_DIR:-}" ] && [ -n "${SEEDS:-}" ] && [ -n "${VOICE_SLOTS:-}" ]; then
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

# The Plus carries the personal library when there is one (it's local only, never committed).
LIB=build/library.bin; [ -s build/library_personal.bin ] && LIB=build/library_personal.bin
cp "$LIB" "$REPO/firmware/library/library.bin"
cp build/intermediate/phrases.txt "$REPO/firmware/library/phrases.txt" 2>/dev/null || true
echo; echo "firmware/library/library.bin <- $LIB ($(stat -c %s "$LIB") bytes)"
echo "reports: $R"
[ "$fail" = 0 ] && echo "BAKE OK" || echo "BAKE HAD FAILURES: read $R/bake.log"
# keep the personal library out of git: firmware/library/library.bin is tracked for the bench cart
[ "$LIB" = build/library_personal.bin ] && git -C "$REPO" update-index --assume-unchanged firmware/library/library.bin
exit $fail
