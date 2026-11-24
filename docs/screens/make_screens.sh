#!/usr/bin/env bash
# Rebuild the README screen pictures from the firmware's own ui.c + form engine, run on the PC.
# Public content only: the ATP 4-02.11 trees in pipeline/plb/content/trees and the made-up word lists
# in public_slots/ (no manual text, no voice). Needs the board-voice firmware (main/vbrain.c,
# components/vform: branch claude/project-thread-lvj6uv until it merges), voice_router with vr/live.py
# (PR #21), Pillow, PyYAML, and cJSON (ESP-IDF's copy, or CJSON=dir holding cJSON.c/.h).
#   FW=<repo with the firmware>  VR=<pipeline/voice_router>  ./make_screens.sh  [OUT=dir]
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$HERE/../.." && pwd)
FW=${FW:-$REPO}; VR=${VR:-$REPO/pipeline/voice_router}; CJSON=${CJSON:-$IDF_PATH/components/json/cJSON}
OUT=${OUT:-$HERE}; W=$(mktemp -d); D=${DATE:-$(date +%F)}; T=$FW/pipeline/plb/content/trees
( cd "$FW/pipeline/plb" && plb build >/dev/null && plb pack --packer "python3 $FW/romkit/pack.py" >/dev/null )
V="$FW/firmware/components/vform/tools"
python3 "$V/formpack.py"   --vr "$VR" --trees "$T" --slots "$HERE/public_slots" -o "$W/form.fpk"
python3 "$V/searchpack.py" --vr "$VR" --trees "$T" --slots "$HERE/public_slots" -o "$W/srch.bin"
python3 "$FW/firmware/tools/nkeypack.py" "$FW/pipeline/plb/build/intermediate/trees.json" -o "$W/nkey.bin"
python3 "$FW/firmware/tools/cart_add.py" "$FW/pipeline/plb/build/library.bin" -o "$W/lib.bin" \
    FORM="$W/form.fpk" SRCH="$W/srch.bin" NKEY="$W/nkey.bin"
S=$FW/firmware/sim
gcc -O2 -DSIM_VOICE -I"$S/../main" -I"$FW/romkit/reader" -I"$S/../components/vform" -I"$CJSON" -o "$W/simv" \
    "$S/sim.c" "$S/../main/ui.c" "$S/../main/sleep_art.c" "$S/../main/vbrain.c" "$FW/romkit/reader/cart.c" \
    "$S/../components/vform/vform.c" "$S/../components/vform/vform_show.c" "$S/../components/vform/vform_search.c" \
    "$CJSON/cJSON.c" -lm
run() { mkdir -p "$W/$1"; local n=$1; shift; "$W/simv" "$W/lib.bin" "$W/$n" plus "$@"; }
run a wl wl wl down
run b1 "speak:she is not breathing"
run b2 "speak:my friend cut his leg with an axe and it is spurting"
run c "speak:my buddy is bleeding" wr "speak:his leg and we have no tourniquet"
run d "speak:my buddy is bleeding" DOWN talk
run e DOWN talk "speak:tourniquet" down
run f sleep
cd "$W"; P="python3 $HERE/strip.py"
$P "$OUT/${D}_screens-buttons-home-to-card_run1_test1.png" "Buttons only: Home > Injury > First aid > Bleeding (public ATP library)" '[["a/00.pbm","Start screen: Injury | Survival"],["a/01.pbm","Injury folder tiles"],["a/02.pbm","Topic tiles"],["a/03.pbm","Cited card, page 1"],["a/04.pbm","DOWN: next page"]]'
$P "$OUT/${D}_screens-voice-life-card-first-sentence_run1_test2.png" "Life threats open on the first sentence (typed input, real form engine)" '[["b1/01.pbm","\"she is not breathing\" -> CPR card"],["b2/01.pbm","\"...cut his leg with an axe, it is spurting\""]]'
$P "$OUT/${D}_screens-voice-form-screen2_run1_test3.png" "Screen 2: the fill-in-the-blanks form, then the cited card" '[["c/01.pbm","\"my buddy is bleeding\": blank 1 of 3"],["c/02.pbm","WIN_R: blank 2 of 3"],["c/03.pbm","\"his leg, no tourniquet\" -> card"]]'
$P "$OUT/${D}_screens-voice-skip-likely-cards_run1_test4.png" "SKIP: hold DOWN on the form for the likely cards, best first" '[["d/01.pbm","Form after \"my buddy is bleeding\""],["d/02.pbm","Hold DOWN: likely cards"],["d/03.pbm","TALK: open the highlighted card"]]'
$P "$OUT/${D}_screens-keyword-search-grid_run1_test5.png" "Keyword search: Menu > Search by word, say one word" '[["e/01.pbm","Hold DOWN: menu"],["e/02.pbm","Search by word"],["e/03.pbm","\"tourniquet\": 4 x 2 ranked tiles"],["e/04.pbm","DOWN: next tile"]]'
$P "$OUT/${D}_screens-sleep-art_run1_test6.png" "Sleep screen (stays on the e-ink with no power)" '[["f/01.pbm","Sleep art"]]'
