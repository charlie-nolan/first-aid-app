# Pocket First-Aid Librarian

An offline, hand-crank e-ink first-aid and survival reference that fits in a pocket.
It runs on a Seeed XIAO ESP32-S3, works with no signal, and **never writes its own advice**:
every screen is a verbatim passage from a public-domain manual, shown with its citation.

> **Not medical advice.** This is a personal prototype, not a certified medical device, and
> most content trees are still unreviewed. In an emergency, call 911 or send an SOS first.
> Get real first-aid training; don't rely on a gadget.

The main target is wilderness first aid with no cell signal: cuts, sprains, fractures,
hypothermia, heat, burns, bites, CPR and choking. Survival is the second half.

## Design rules

These decide everything else:

1. **Tiny hardware on purpose.** Keep the needed intelligence narrow and make the software fit an
   ESP32-S3, instead of adding bigger co-processors.
2. **Buttons first.** It must work perfectly with buttons alone. Simple trees, one button per answer,
   no topic more than 3 presses deep. Voice is an extra on top.
3. **No AI on the device.** Fully offline, no generative model, ever.
4. **Never invents text.** Only signed-off trees of verbatim, cited passages. Emergency pages are
   always reachable and never hidden.
5. **Everyday wilderness topics first.**

## How it works

### The cartridge (`romkit/`)

The content lives in `library.bin`, a Game Boy-style cartridge read straight from flash, with no
database and no filesystem:

- dictionary-coded text (Re-Pair + Huffman),
- 1-bit pictures (context model + range coder),
- a tree table for the menus and answer cards,
- a small C reader (`romkit/reader/cart.c`) that the firmware uses in place.

The format spec is in [`romkit/FORMAT.md`](romkit/FORMAT.md). The whole public library is about
3.7 MB today, against a 6-8 MB budget on a 16 MB flash.

### The content pipeline (`pipeline/plb/`)

A PC-side builder turns manual PDFs into cited content:

manual PDFs, pinned by sha256 → numbered paragraphs and figures → cited YAML trees → `romkit` packs the cartridge.

Rules enforced in code: verbatim passages only, labels of 40 characters or fewer, every
"use your kit / improvise" branch has a "nothing" option, figures marked © or "used with
permission" are refused, and there are no mushroom-foraging pages. Known-outdated advice in older
manuals (for example the constricting band for snakebites) is never used. `plb verbatim` checks card text against the PDF page, and `plb voice`
tests the voice phrases with synthetic utterances.

**Sources** are US Government works only: ATP 4-02.11 (primary first aid), TC 4-02.1, ATP 3-50.21
(survival), AFH 10-644, the mountain, cold-weather, jungle and desert manuals, TB MEDs 505/507/508,
the Ship's Medicine Chest, Navy *Poisonous Snakes of the World*, FEMA CERT, NWS and Ready.gov pages,
and others. Full list and the public-domain rule in [`pipeline/SOURCES.md`](pipeline/SOURCES.md).
All converted manuals also ship as a text-only "Unreviewed" reference shelf, kept separate from the
signed-off topics.

### The firmware (`firmware/`)

ESP-IDF 5.3 C firmware: e-ink, buttons, lights, deep sleep, battery and push-to-talk voice.
`firmware/sim/` renders the screens on a PC. Bench wiring and flashing are in
[`firmware/README.md`](firmware/README.md).

- **Start screen:** two tiles, Injury and Survival, each opening a tree of folder and topic tiles.
- **Answer cards:** a warning box, one verbatim step per screen, and TALK shows the full cited passage.
  Unreviewed trees carry an "Unreviewed" marker.
- **Context tags:** activity, season and region (US state) hide content that doesn't apply.
  Emergency pages are never hidden.
- **Windows:** 5 virtual windows kept in RTC memory, so waking returns to the last page.
- **Fonts:** Lexend Deca or Bitter at 10, 12 or 15 px.
- **Voice (optional):** ESP-SR fixed phrases (no model). Voice only opens answer cards from signed-off
  trees; it never searches raw manuals.

## Hardware

| Part | Choice |
|---|---|
| Brain | Seeed XIAO ESP32-S3 Plus (16 MB flash); plain XIAO S3 on the bench |
| Screen | Waveshare 2.13" B/W e-ink, 250×122, edge-lit by warm-white LEDs |
| Buttons | 6 sealed buttons, each on its own RTC GPIO (window left/right, up/down, TALK, BACK) |
| Mic | PDM microphone, powered from a GPIO |
| Rescue signals | 3 W white bead (flashlight, Morse SOS), 3 W 850 nm IR bead (SOS for night vision), CPR cadence LED, Meshtastic LoRa SOS on a power-switched second board |
| Battery | 300 mAh LiPo |
| Charging | Hand crank (N20 gear motor, bevel gears, bridge rectifier, buck-boost regulator) and USB-C. A supercap bank lets the crank run the board with a dead or frozen battery; a temperature switch stops charging below about 2 °C |
| Case | About 91 × 38 × 21 mm, PETG, gasketed, M2 heat-set inserts |

Pin map: `firmware/main/board.h`. Parts lists, wiring and enclosure files will go in `hardware/`.

## Repository layout

| Folder | Contents |
| --- | --- |
| `firmware/` | ESP-IDF C firmware and the PC screen simulator |
| `romkit/` | Cartridge format: packer, text and picture codecs, font packer, C reader, tests |
| `pipeline/` | `plb`, the PDF-to-cartridge builder, plus GPU helper scripts |
| `docs/` | Notes on the guide content |
| `hardware/` | Parts lists, wiring and enclosure files (coming) |
| `model/` | Reserved; v1 has no model |

`firmware/components/cart` points at `../../../romkit/reader`, so keep `firmware/` and `romkit/`
side by side. CI runs the content schema check, the pipeline tests and the cartridge reader tests
on every pull request, using only test fixtures (no manual PDFs needed).

## Status

Prototype on the breadboard: a plain XIAO S3 drives the e-ink with the cartridge format and the public
library. The Bleeding tree is merged; CPR and choking are drafted and unreviewed. Next on the bench:
the light and strobe stations, crank and supercap tests, and the full 6-button board.

## License

- Code: [GPL-3.0](LICENSE).
- Fonts in `romkit/fonts/`: SIL Open Font License 1.1 ([`romkit/fonts/OFL.txt`](romkit/fonts/OFL.txt)).
- Content passages and figures: US Government works, public domain (17 USC 105). Citations are kept
  on every card.
