# Voice tether (bench prototype)

A rough firmware for trying the voice flow on the bench board with your own voice, before the
form logic moves to C. The board is the mic, the screen and the buttons; the PC is the brain.

1. Hold **TALK** and speak. The clip (16 kHz mono, 500 ms pre-roll) goes over USB.
2. `pc_bridge.py` decodes it with the speech model (fasr), runs the fill-in-the-blanks form
   (`pipeline/voice_router/vr/form.py`, 04d654f or later; with fasr 3b0e3a6+ (decode() with the calibrated reject)) and sends back one screen as JSON.
3. The board draws the form (each open field with its choices), the SKIP list, or the card
   that opens (title, cites, node id; no card text). Every screen can be answered with buttons.

Buttons (plain XIAO S3: TALK D0, BACK D1, DOWN D2):

| | tap | hold |
|---|---|---|
| TALK | choose the highlighted choice / open the highlighted card | speak (recording starts at 300 ms) |
| DOWN | next choice (runs on into the next field) | SKIP: the cards the blanks allow |
| BACK | previous choice | new problem |

Mic wiring is the recorder's: PDM CLK on D4, DAT on D5 (take the battery divider off D5).

## Flash (app slot only; the library with the fonts stays in flash)

    . ~/esp/esp-idf/export.sh
    cd firmware/tether && idf.py -B build build
    python -m esptool --chip esp32s3 -p /dev/ttyACM0 write_flash 0x10000 build/vr_tether.bin

## Run the PC side

    WORK=/mnt/data/first-aid-gpu ~/miniconda3/envs/firstaid/bin/python firmware/tether/pc_bridge.py \
        --vr <voice_router folder at 04d654f or later; with fasr 3b0e3a6+ (decode() with the calibrated reject)> \
        --trees ~/first-aid-trees/pipeline/plb/content/trees_personal ~/first-aid-build/pipeline/plb/content/trees \
        --slots ~/first-aid-voice/slots --fasr ~/first-aid-speech/pipeline/speech

Tree order matters (first hit wins): trees_personal first, as the parity set was built.

Add `--typed` to type sentences instead of speaking. Each run writes to
`$WORK/speech/tether/<date_time>/`: `clips/` (every take, as training data), `screens/` (a PNG of
every screen the board drew, from its own framebuffer) `turns.jsonl` (one line per turn, the shape of vr.live_web's, with the scenario id) and `log.jsonl`
(every SHOW, PICK and board line). Nothing from a run goes into the repo except screenshots picked for `docs/`.

## Link format (USB serial, text lines)

PC to board: `HELLO`, `ACK <id>`, `RESEND`, `SHOW <json>` (one line, ASCII, up to 8 KB).

Board to PC: `VR-TETHER 1 <pending>`; `TAKE {json}` + `D <base64>` lines + `END <crc32>` (as the
recorder); `PICK {json}`; `FB <base64>` (the 250 x 122 framebuffer, 1 = black); `LOG <text>`.

    SHOW {"v":1,"seq":N,"kind":"form"|"card"|"skip"|"again"|"start","heard":"..","msg":"..",
          "fields":[{"key":"@topic"|slot,"q":"..","choices":[{"label":..,"value":..}],"after":[slot,value]|null}],
          "top":[{"title":..,"node":"tree/card"}],
          "card":{"title":"Tree > Card","cites":[..],"node":"tree/card"},
          "cards":[{"title":..,"node":..}]}
    PICK {"seq":N,"field":"<key>","value":"<value>","label":"<label>"}      -> vr.form.pick(key, value)
    PICK {"seq":N,"field":"<key>","value":null,"node":"tree/card"}          -> plain tile: vr.form.pick(key, None, to=node)
    PICK {"seq":N,"action":"skip"|"new"|"open","node":"tree/card"}          -> skip() / start over / card

The board keeps at most 6 fields of 12 choices and 6 SKIP cards; longer strings are cut.

## Board brain (the form runs on the board, in C)

Build with `CONFIG_TETHER_BOARD_BRAIN=y` and the form pack (`components/vform/tools/formpack.py`;
the real trees are personal, so the pack is built locally and never committed):

    . ~/esp/esp-idf/export.sh
    printf 'CONFIG_TETHER_BOARD_BRAIN=y\n' > /tmp/brain.cfg
    idf.py -B build-brain -D SDKCONFIG=build-brain/sdkconfig -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;/tmp/brain.cfg" \
        -D TETHER_FPK=/tmp/real.fpk build
    python -m esptool --chip esp32s3 -p /dev/ttyACM0 write_flash 0x10000 build-brain/vr_tether.bin

Then run the bridge with `--board-brain`. The PC only turns speech into words and sends
`WORDS {"hyps": [...], "reject": N}` (and `BRAIN-RESET` when it (re)connects, so both screen counters restart); the board builds every screen itself (same bytes as vr.form),
draws it, and echoes it as `SHOWN <json>`. Taps, SKIP, new and open are answered on the board (still
echoed as PICK for the log). The bridge runs vr.form in the shadow on the same inputs and marks every
turn `shadow_match` true/false in turns.jsonl, so each bench session is also a live parity check.
The board logs `LOG brain <ms>` per screen (how long the C took).

Without the board: `python fake_board.py --bin /tmp/vform_test --pack /tmp/real.fpk` opens a
pseudo-terminal that behaves like a board-brain board (the same C engine, through host_test).

## Not yet

- Card text from `library.bin` by node (needs the PC node id mapped to a cartridge node).
- Several problems at once: the bench board has no window keys, so only the first is shown.
- Speech on the board (the PC still decodes; the DIY int8 engine slot is `components/asr`).
