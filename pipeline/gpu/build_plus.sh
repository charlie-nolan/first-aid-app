#!/usr/bin/env bash
# Build (and optionally flash) the firmware for the XIAO ESP32-S3 Plus with the baked library.
#   ./build_plus.sh                    build only
#   ./build_plus.sh --flash            build + flash (port auto-detected) + serial monitor
#   ./build_plus.sh --bench --flash    voice bench build for bench_loop.py (never sleeps)
set -euo pipefail
source "$(dirname "$0")/common.sh"
BENCH=0; FLASH=0
for a in "$@"; do case "$a" in --bench) BENCH=1;; --flash) FLASH=1;; esac; done
IDF="${IDF_PATH:-$HOME/esp/esp-idf}"
# shellcheck disable=SC1091
source "$IDF/export.sh" >/dev/null
cd "$REPO/firmware"
DEFS="sdkconfig.defaults;sdkconfig.plus"; B=build-plus
[ "$BENCH" = 1 ] && { DEFS="$DEFS;sdkconfig.voicebench"; B=build-plus-bench; }
ls -la library/library.bin
idf.py -B "$B" -D SDKCONFIG="$B/sdkconfig" -D "SDKCONFIG_DEFAULTS=$DEFS" set-target esp32s3 build 2>&1 | tee "$LOGS/build_plus.log"
if [ "$FLASH" = 1 ]; then
    idf.py -B "$B" -D SDKCONFIG="$B/sdkconfig" -D "SDKCONFIG_DEFAULTS=$DEFS" flash
    [ "$BENCH" = 1 ] && echo "Bench build flashed. Now: python bench_loop.py --clips \$WORK/clips" || idf.py -B "$B" monitor
fi
