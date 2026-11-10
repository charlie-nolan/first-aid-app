#!/usr/bin/env bash
# Bake the cards: build, every check, pack library.bin (public, then personal if you have
# sources.personal.yaml), verbatim check against the PDFs, voice report, review pages.
# Then copy the library the Plus should carry into firmware/library/.
# CPU only, a few minutes. Reports land in $WORK/reports.
set -uo pipefail
source "$(dirname "$0")/common.sh"; activate
cd "$PLB"
R="$WORK/reports/$(date +%Y%m%d-%H%M)"; mkdir -p "$R"
PACK="python3 $REPO/romkit/pack.py"
fail=0
step() { echo; echo "== $*"; "$@" 2>&1 | tee -a "$R/bake.log"; local rc=${PIPESTATUS[0]}; [ "$rc" = 0 ] || { echo "!! failed ($rc): $*"; fail=1; }; return 0; }

step plb validate
step plb pack --packer "$PACK"
cp build/library.bin "$R/library.bin" 2>/dev/null
step plb verbatim --out "$R/verbatim.md"
step plb voice --out "$R/voice.md"
step plb review --out "$R/review"
if [ -f sources.personal.yaml ]; then
    step plb --personal pack --packer "$PACK"
    step plb --personal verbatim --out "$R/verbatim-personal.md"
    step plb --personal voice --out "$R/voice-personal.md"
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
