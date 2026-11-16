# XIAO ESP32-S3 Plus bring-up and voice plan

Charlie said yes to this on 2026-11-13. Buttons stay primary and must work alone.
No generative model goes on the device.

## Budget

| | Flash (16 MB) | PSRAM (8 MB) | Internal SRAM (512 KB) |
|---|---|---|---|
| ESP-SR MultiNet 7 (quantised) | ~4-5 MB (`model`, 6 MB partition) | ~2 MB | audio chunk buffers |
| Firmware | ~1.5 MB (`factory`, 1.94 MB) | - | stacks, framebuffer (4 KB) |
| Library | 2-3 MB now, up to ~6 (`library`, 8 MB) | - | read straight from flash (mmap) |
| Optional meaning classifier (ESP-DL, 8-bit) | 1-3 MB | 1-3 MB | - |

Cores: core 0 runs the mic, plus the AFE if we add one (and the PSRAM soak test). Core 1
runs recognition (the `voice` task), any classifier, and the UI.

## Voice: topic first, then numbers (Charlie, 2026-11-13)

- **Home, the tile screens and "Did you mean...":** the full topic phrase list
  (`cart_voice_list` for Home), plus the number words.
- **Inside a topic:** the answer tiles show **1-4** in the corner by their button, in
  reading order (1 top left, 2 top right, 3 bottom left, 4 bottom right). The list is:
  - `one / number one / option one` ... `four / ...`
  - `back / go back`, `again / read again / repeat`, `home / go home / start over`
  - every section's entry phrase and every `always: true` phrase, so plans can change
    from any screen (Charlie: "keep the others in every screen ... but primarily
    rely on 1-4"). Per-answer phrases are left out, because the numbers do that job.
- A number does the same thing as pressing that tile's button. On a question's text
  pages, a number brings up the tiles first, so you always see what you're picking.
- Numbers are strict. Below `LIB_VOICE_KEY_MIN_PCT` (50 %), or when a different number
  scores within 10 points, the screen says "Say the number again" and does not guess
  a branch.
- Sound-alikes: "to/too" can only land on 2 and "for/fore" only on 4, because nothing
  else in the list sounds like them. "Tree" lands on 3.
- The list is swapped (`esp_mn_commands_update`) when you enter or leave a topic,
  never mid-speech. The serial log prints how long each swap takes.

Kconfig (menuconfig > Librarian): `LIB_VOICE_NUMBERS` (on), `LIB_VOICE_TOPICS_EVERYWHERE`
(on), `LIB_VOICE_KEY_MIN_PCT` (50), `LIB_VOICE_MAX_JUMPS` (only when topics-everywhere is off).

## Bring-up order

All builds come from `pipeline/gpu/build_plus.sh` on the desktop, and each variant gets
its own build folder.

1. **Current build working on the Plus.** Run `build_plus.sh --flash`. Check:
   - all 6 buttons
   - screen (BUSY on D17)
   - battery on GPIO10
   - push-to-talk: hold TALK, say "bleeding", then say "one"
   - the serial log shows `voice list: ... swap N ms` and `result ... after release: N ms`

   Pin moves from the bench: BUSY D3->D17, DOWN D2->D5, UP D4, WIN L/R D2/D3. Unplug
   the LED gate jumpers from D4/D5 first.
2. **PSRAM speed.** Run `--psramtest` first to get the 80 MHz baseline numbers. Then run
   `--psram120`, which is Espressif's experimental 120 MHz octal PSRAM + flash. Let each
   one run at least 30 min, warm and cold (fridge). The log shows
   `PSRAM pass N err E wr/rd/cp MB/s temp C`. Keep 120 MHz only if every run shows err 0.
3. **Recogniser.** MultiNet 7 is the default. `--mn6` builds MultiNet 6 for comparison.
   For each one, use `--bench` and run `make_clips.py --keys` and the topic clips
   through `bench_loop.py`, and compare hits, misses, false triggers and latency.
   `--oldvoice` (per-section phrases) and `--jumpsonly` (numbers + life-threat jumps
   only) are the A/B checks for the number design.
4. **Optional: 8-bit meaning classifier (ESP-DL)**, only if real asks at Home miss more
   than ~1 in 10. It maps an utterance to the top 3 topic cards, and a confirm screen
   follows. Time it on core 1 next to MultiNet.

## Bench commands

```
cd pipeline/gpu
./build_plus.sh --flash                      # step 1
./build_plus.sh --psramtest --flash          # step 2 baseline, then --psram120
./build_plus.sh --bench --flash              # step 3, MultiNet 7
python make_clips.py --keys --out $WORK/keyclips
python bench_loop.py --clips $WORK/keyclips --tree 0
./build_plus.sh --bench --mn6 --flash        # same clips again
```

`firmware/sim` shows the numbered tiles on a PC: `sim LIB.bin OUT plus num:1 num:2 key:back`.
