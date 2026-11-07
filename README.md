# Pocket First-Aid Librarian

An offline, hand-crank e-ink first-aid and survival reference (Seeed XIAO ESP32-S3).
It never writes its own advice: every page is a vetted passage from a public-domain
manual, shown with its citation. Wake screen: "Life threat? Call 911 / SOS first."

## Layout

| Folder | Contents |
| --- | --- |
| `firmware/` | ESP-IDF 5.3 C firmware for the device (e-ink, buttons, lights, sleep, voice). See `firmware/README.md` for bench wiring and flashing. `firmware/sim/` renders screens on a PC. |
| `romkit/` | The `library.bin` cartridge format: packer, text/picture codecs, font packer and the C reader (`romkit/reader/cart.c`, used by the firmware in place). Spec in `romkit/FORMAT.md`. |
| `pipeline/` | `pipeline/plb/`: PC builder that turns manual PDFs into cited content trees and calls romkit to pack them. Source list in `pipeline/SOURCES.md`. |
| `docs/first-aid/`, `docs/survival/` | Notes on the guide content |
| `hardware/` | Parts lists, wiring and enclosure/CAD files |
| `model/` | Reserved for a later on-device model (v1 uses fixed ESP-SR voice phrases, no model) |

`firmware/components/cart` points at `../../../romkit/reader`, so keep `firmware/` and
`romkit/` side by side.

## Status

Not yet compiled against real ESP-IDF. First bench build targets the plain XIAO S3.
