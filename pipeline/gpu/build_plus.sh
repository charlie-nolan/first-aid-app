#!/usr/bin/env bash
# Build (and optionally flash) the firmware for the XIAO ESP32-S3 Plus with the baked library.
#   ./build_plus.sh                    build only
#   ./build_plus.sh --flash            build + flash (port auto-detected) + serial monitor
#   ./build_plus.sh --bench --flash    voice bench build for bench_loop.py (never sleeps)
# Bring-up experiments (combine freely; each gets its own build folder):
#   --psramtest   PSRAM soak test at the normal 80 MHz (baseline)
#   --psram120    EXPERIMENTAL 120 MHz PSRAM + flash, with the soak test
#   --mn6         English MultiNet 6 instead of 7, to compare
#   --oldvoice    per-section phrase lists instead of numbered tiles
#   --jumpsonly   numbered tiles, but only life-threat jumps live inside a topic
set -euo pipefail
source "$(dirname "$0")/common.sh"
BENCH=0; FLASH=0; EXTRA=""; TAG=""
for a in "$@"; do case "$a" in
    --bench) BENCH=1;; --flash) FLASH=1;;
    --psramtest|--psram120|--mn6|--oldvoice|--jumpsonly) n="${a#--}"; EXTRA="$EXTRA;sdkconfig.$n"; TAG="$TAG-$n";;
    *) echo "unknown option $a"; exit 2;;
esac; done
IDF="${IDF_PATH:-$HOME/esp/esp-idf}"
# shellcheck disable=SC1091
source "$IDF/export.sh" >/dev/null
cd "$REPO/firmware"
DEFS="sdkconfig.defaults;sdkconfig.plus"; B=build-plus
[ "$BENCH" = 1 ] && { DEFS="$DEFS;sdkconfig.voicebench"; B=build-plus-bench; }
DEFS="$DEFS$EXTRA"; B="$B$TAG"
# IDF 5.3 ignores $CONFIG{} in idf_component.yml before sdkconfig exists, so the Plus
# build uses its own lock file with ESP-SR in it (the plain build has none).
LOCK=(); [ -f dependencies.plus.lock ] && LOCK=(-D DEPENDENCIES_LOCK=dependencies.plus.lock)
ls -la library/library.bin
echo "build $B with $DEFS"
idf.py -B "$B" "${LOCK[@]}" -D SDKCONFIG="$B/sdkconfig" -D "SDKCONFIG_DEFAULTS=$DEFS" set-target esp32s3 build 2>&1 | tee "$LOGS/build_plus.log"
if [ "$FLASH" = 1 ]; then
    idf.py -B "$B" "${LOCK[@]}" -D SDKCONFIG="$B/sdkconfig" -D "SDKCONFIG_DEFAULTS=$DEFS" flash
    [ "$BENCH" = 1 ] && echo "Bench build flashed. Now: python bench_loop.py --clips \$WORK/clips" || idf.py -B "$B" "${LOCK[@]}" monitor
fi
