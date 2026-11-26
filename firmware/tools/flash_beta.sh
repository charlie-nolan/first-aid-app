#!/bin/bash
# Flash the beta firmware (main firmware + board voice) onto a board, bootloader to library:
#   tools/flash_beta.sh plain|plus <build dir> <library_voice.bin> [port]
# build dir: an idf.py -B folder built with sdkconfig.beta (see sdkconfig.beta). The voice library is personal
# (library_personal + FORM/ASRM/LEXI/ASRC/NKEY/SRCH, tools/cart_add.py): it never goes into git.
# A board that already has this partition table and library can take just the app: add APP_ONLY=1.
set -euo pipefail
board=$1; b=$2; cart=$3; port=${4:-$(ls /dev/ttyACM* | head -1)}
case $board in
  plain) lib=0x210000; max=$((0x5F0000));;
  plus)  lib=0x110000; max=$((0xEF0000));;   # partitions-16mb-beta.csv (sdkconfig.plus-beta)
  *) echo "board: plain or plus"; exit 2;;
esac
size=$(stat -c %s "$cart")
if [ "$size" -gt "$max" ]; then echo "cart is $size bytes, the $board library partition holds $max"; exit 1; fi
if fuser "$port" >/dev/null 2>&1; then echo "$port is in use: stop the program using it first"; exit 1; fi
grep -q "^CONFIG_LIB_VOICE_DIY=y" "$b/sdkconfig" 2>/dev/null || grep -q "^CONFIG_LIB_VOICE_DIY=y" "$b/config/sdkconfig.h" 2>/dev/null \
  || grep -q "CONFIG_LIB_VOICE_DIY 1" "$b/config/sdkconfig.h" || { echo "$b is not a beta build (LIB_VOICE_DIY)"; exit 1; }
if [ "$board" = plus ] && ! grep -q "CONFIG_LIB_BOARD_PLUS 1" "$b/config/sdkconfig.h"; then echo "$b is not a Plus build"; exit 1; fi
if [ "$board" = plain ] && grep -q "CONFIG_LIB_BOARD_PLUS 1" "$b/config/sdkconfig.h"; then echo "$b is a Plus build"; exit 1; fi
if [ "$board" = plus ] && ! grep -q 'partitions-16mb-beta.csv' "$b/sdkconfig" 2>/dev/null; then echo "$b is not built with sdkconfig.plus-beta (library at 0x110000)"; exit 1; fi
. ~/esp/esp-idf/export.sh >/dev/null 2>&1
if [ "${APP_ONLY:-0}" = 1 ]; then
  python -m esptool --chip esp32s3 -p "$port" -b 921600 write_flash 0x10000 "$b/librarian.bin"
else
  python -m esptool --chip esp32s3 -p "$port" -b 921600 write_flash 0x0 "$b/bootloader/bootloader.bin" \
      0x8000 "$b/partition_table/partition-table.bin" 0x10000 "$b/librarian.bin" $lib "$cart"
fi
