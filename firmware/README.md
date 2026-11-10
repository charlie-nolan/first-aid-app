# Librarian firmware (ESP32-S3)

Firmware for the Pocket First-Aid Librarian. Written in C on **ESP-IDF 5.3**,
Espressif's own toolkit for the ESP32-S3. It reads the cartridge
(`library.bin`) in place from flash with the romkit reader
(`../romkit/reader/cart.c`, shared, not copied), draws pages on the 2.13" e-ink,
and handles buttons, lights, deep sleep, battery and push-to-talk voice.

Why ESP-IDF rather than Arduino: the voice engine (ESP-SR) is an ESP-IDF
component, deep sleep with RTC memory and the flash-mapped cartridge are
first-class there, and the reader is plain C already.

## What works on which board

| | Plain XIAO S3 (bench) | XIAO S3 Plus |
|---|---|---|
| E-ink, cartridge pages, pictures, 3 text sizes | yes | yes |
| Buttons | TALK, BACK, DOWN | all 6 |
| White light (front light, flashlight, SOS) | D4 (one pin for both) | front light D19, white bead D18 |
| IR SOS flash (shows on night-vision goggles) | D5 | D11 |
| Deep sleep, windows kept in RTC memory | yes | yes |
| Battery %, reserve lock, crank detection | no battery pin | yes (GPIO10) |
| Voice (hold TALK) | no mic | yes |
| Mesh SOS power switch | no | D13 |

## Bench wiring (plain XIAO S3)

| XIAO pad | GPIO | Goes to |
|---|---|---|
| D0 | 1 | TALK button to GND |
| D1 | 2 | BACK button to GND |
| D2 | 3 | DOWN button to GND |
| D3 | 4 | HAT BUSY (Pi pin 18) |
| D4 | 5 | white LED: start with a normal LED + 330 ohm to GND; later the MOSFET gate |
| D5 | 6 | IR LED: same, normal LED first |
| D6 | 43 | HAT DC (Pi pin 22) |
| D7 | 44 | HAT RST (Pi pin 11) |
| D8 | 7 | HAT CLK (Pi pin 23) |
| D9 | 8 | HAT CS (Pi pin 24) |
| D10 | 9 | HAT DIN (Pi pin 19) |
| 3V3 / GND | | HAT 3.3 V (Pi pin 1) and GND (Pi pin 6) |

Buttons use the chip's internal pull-ups, so each one just goes from its pin to
GND. When you move to the MOSFETs, put a 100 k resistor from each gate to GND so
the beads stay off while the board boots or sleeps.

The Plus pin map is in `main/board.h` and matches the hardware doc.

## One-time setup

1. Install ESP-IDF **v5.3** (any 5.3.x):
   - Windows: the "ESP-IDF Tools Installer" (online), pick v5.3.
   - Mac/Linux: `git clone -b v5.3.2 --recursive https://github.com/espressif/esp-idf.git ~/esp/esp-idf`
     then `cd ~/esp/esp-idf && ./install.sh esp32s3`.
   - Or VS Code with the "ESP-IDF" extension, which installs it for you.
2. Open the "ESP-IDF terminal" (Windows) or run `. ~/esp/esp-idf/export.sh` (Mac/Linux).

## Flash the plain S3 (bench build)

From this `firmware` folder, with the board plugged in by USB-C:

    idf.py -B build-plain -D SDKCONFIG=build-plain/sdkconfig -D "SDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.plain" flash monitor

That builds, writes the app **and** `library/library.bin` (the test cartridge),
and opens the serial log. Quit the log with `Ctrl+]`. If it can't find the port,
add `-p COM5` (Windows) or `-p /dev/ttyACM0` (Linux) or `-p /dev/cu.usbmodem*` (Mac).
If flashing won't start: hold the BOOT button, tap RESET, release BOOT, try again.

The first build takes a few minutes; after that only changed files rebuild.

## Flash the Plus

    idf.py -B build-plus -D SDKCONFIG=build-plus/sdkconfig -D "SDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.plus" flash monitor

This also downloads ESP-SR and writes its English speech model to the `model` partition.

To change settings (white bead pin, idle time, battery thresholds, flip screen):
add `menuconfig` before `flash`, then look under **Librarian**.

## What you should see

1. The wake screen: "Life threat? Call 911 / SOS first." (full refresh, ~2 s).
2. Any button: the bench test page. The header shows window number, page title,
   page n/N (and battery % on the Plus); the footer shows the citation.
3. Controls:

| Button | Short press | Hold (0.7 s) |
|---|---|---|
| DOWN | next page / next choice (wraps on the plain board) | Menu: flashlight, SOS (white or IR), mesh, text size, font (Lexend/Bitter), where and when, library, sleep |
| UP (Plus) | previous page / choice | Library (all trees) |
| TALK | jump to the choices, then open the highlighted one | Plus: speak while held, release to look it up |
| BACK | go back; at the start of a window, the library | step the front light: off, dim, bright |
| Window L / R (Plus) | switch between the 5 windows | smaller / larger text |

**Answer screens (Plus).** When a question has 4 answers or fewer, its last page
puts each answer in a screen corner, next to its button: top left = Window L,
top right = Window R, bottom left = UP, bottom right = DOWN. One press answers.
TALK goes back to the question's text, BACK to the previous question, and a
spare bottom-left corner turns back a page. The voice "Did you mean..." picker
works the same way. The plain bench board has no corner buttons, so it keeps the
list (DOWN to move, TALK to open). Trees are capped at 4 answers per question
(`plb build` fails otherwise), so every card works with buttons alone.

**Voice bench build (Plus).** `sdkconfig.voicebench` adds a serial test mode for
`pipeline/gpu/bench_loop.py`: send `L <ms>` to listen, `T <tree>` to load a section's
voice list; each result prints as a `VB ...` line and the board never sleeps. Build it
with `pipeline/gpu/build_plus.sh --bench --flash`.

Quick (partial) refreshes leave faint ghosts; every 5th refresh is a full one
(the black-white flash), and pressing any two buttons together forces one.
The serial log prints each refresh and how long BUSY stayed high. "BUSY never
went high" means the BUSY wire (D3) is loose; the screen then tears, so the
firmware falls back to fixed waits until it is fixed.

4. After 60 s without a press, it draws the sleep card and deep-sleeps. Any
   button wakes it back to the wake screen; the windows are where you left them.

## Where and when (sticky context)

Menu > "Where and when..." picks a state, season and activity (any of them can
stay "Any"). Choices and whole sections that don't fit are hidden; emergency
pages always show. The choice is saved in flash (NVS), so it stays through
sleep, power loss and app updates until you change it. On the Plus the voice
command list follows the section you're reading and this context
(`cart_voice_list` from the ROM thread), so MultiNet only listens for phrases
that make sense right now.

## Test cartridge

`library/library.bin` is a bench-only cartridge with no first-aid content
(every page says so). Rebuild it with `python3 tools/make_test_library.py`
(needs numpy, Pillow, freetype-py and uharfbuzz: `pip install numpy pillow freetype-py uharfbuzz`). For a real cartridge from the pipeline, copy its
`library.bin` and `phrases.txt` into `library/` and flash again.

## Preview screens on a PC

`sim/` runs the same screen code on a computer and saves every screen:

    gcc -O2 -I main -I ../romkit/reader -o sim/sim sim/sim.c main/ui.c ../romkit/reader/cart.c
    mkdir -p out && sim/sim library/library.bin out plus talk down talk down talk back DOWN
    python3 sim/montage.py out screens.png

## Files

- `main/board.h`: both pin maps. `main/Kconfig.projbuild`: the Librarian menu.
- `main/ui.c`: screens, navigation, windows, menu (no ESP-IDF calls; also runs in `sim/`).
- `main/epd.c`: SSD1680 driver for the Waveshare 2.13" V4 (full + partial refresh).
- `main/buttons.c`, `main/lights.c`, `main/power.c`, `main/voice.c`, `main/main.c`.
- `partitions-8mb.csv` / `partitions-16mb.csv`: app, (voice model), library.

## Known gaps

- Not yet compiled against ESP-IDF in the build environment: the first build on
  your PC is the first real compile. Send the error text if it fails.
- Voice runs MultiNet without Espressif's audio front end (noise suppression);
  add it if recognition is poor outdoors.
- Wide "pan" pictures show the centre 250 px; panning is not wired to buttons yet.
- The Meshtastic board's SOS trigger (Detection Sensor) still needs its bench test.
- UI words (menu items, "Battery low. Crank to open this page...") are firmware
  text, not cartridge text; they need Charlie's sign-off like strings.yaml.
