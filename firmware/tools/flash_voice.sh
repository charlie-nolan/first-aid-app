#!/bin/bash
# Flash a voice test onto the bench board: the voice cart into the library partition and a tether app.
#   tools/flash_voice.sh plain|plus <library_voice.bin> <app.bin> [port]
# plain XIAO S3: library at 0x210000 (partitions-8mb.csv); Plus: 0x800000 (partitions-16mb.csv).
# The app goes to 0x10000 (factory). Build the app for the same board (sdkconfig.plus for the Plus).
# The cart is personal (library_personal + FORM/ASRM/LEXI/ASRC/NKEY): it never goes into git.
set -euo pipefail
board=$1; cart=$2; app=$3; port=${4:-$(ls /dev/ttyACM* | head -1)}
case $board in
  plain) lib=0x210000; max=$((0x5F0000));;
  plus)  lib=0x800000; max=$((0x800000));;
  *) echo "board: plain or plus"; exit 2;;
esac
size=$(stat -c %s "$cart")
if [ "$size" -gt "$max" ]; then echo "cart is $size bytes, the $board library partition holds $max"; exit 1; fi
if fuser "$port" >/dev/null 2>&1; then echo "$port is in use (fasr devrec or the bridge?): stop it first"; exit 1; fi
. ~/esp/esp-idf/export.sh >/dev/null 2>&1
python -m esptool --chip esp32s3 -p "$port" -b 921600 write_flash $lib "$cart" 0x10000 "$app"
