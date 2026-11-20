# Mesh menu and SOS (v2 plan, approved by Charlie 2026-11-20; build after the current board tests)

## Hardware

- **Board 2:** Seeed kit 102010611 (XIAO ESP32-S3 + Wio-SX1262), stock Meshtastic firmware, with a Seeed L76K
  GNSS stacked on top (BN-880 on the bench).
- **Power:** AO3401 high-side switch. The Plus drives **D13 (GPIO40, `PIN_MESH_PWR`) LOW = on**; a 100 k
  pull-up keeps board 2 **off by default**.
  - The Plus must **hold D13 through deep sleep** (RTC GPIO hold) while a mode needs board 2 on.
  - After any Plus reset board 2 must come up **off** (pin released = pull-up = off) unless a saved
    mode (SOS, Relay) is restored on purpose at boot.
- **Link:** the Plus talks to stock Meshtastic over its **BLE API** (not ESP-NOW, no extra pins): send
  a text, read back the GPS fix for "Where am I".

## Modes (hold-DOWN menu > "mesh")

| Mode | Board 2 | Ends |
|---|---|---|
| **Off** (default) | off | - |
| **SOS** | on, GPS fix, beacon | after 12 h unless renewed; or at the battery floor (GPIO10 ADC) |
| **Where am I** | on until a fix, then off | shows lat/long on the Plus |
| **Relay** | on as a mesh node | the user turns it off, or the battery floor |

## SOS message

One Meshtastic text, about 90 characters and **at most ~150**, re-sent every few minutes with a fresh fix:

    SOS 47.6123,-121.2345 ±10m 14:05 | leg fracture, can't walk | ~30 yo M | awake, breathing | 2 in group

- Injury and state come **only from intake-form blanks the user confirmed** (never guessed).
- Age band and sex come from a one-time **"about me"** setting, saved in **NVS**.
- **No names, no medical history**: the channel is public.

## Firmware pieces (to build)

1. Mesh power manager: D13 control with RTC hold, saved mode in NVS, 12 h timer, battery floor.
2. BLE central to the Meshtastic phone API (protobuf ToRadio/FromRadio): send text, read position.
3. Menu pages: mode picker, "Where am I" (lat/long, accuracy, time of fix), SOS status (last sent, next send).
4. SOS composer from the form engine's confirmed blanks (vform `filled`) + "about me".
