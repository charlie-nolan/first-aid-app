# Cranked-power mode: running on the supercaps alone (plan, from the hardware thread 2026-11-20)

When the LiPo is **dead**, or **blocked by the MCP9509H cold cutoff** (below about +2 °C), the board
runs on the supercap bank (2 x 3 F in series, ~5.4 V) through an SS14 into the XIAO 5 V pin.

**Energy:** ~8 J per fill after 10-20 s of cranking: roughly **8 page turns**, or **2 voice queries**
(~3 J each). Voice is the expensive part.

**Detection:** the battery reading on GPIO10 (`PIN_BAT_ADC`) is near 0 V or invalid while the board is
running.

## What the firmware does in this mode

1. Show a **"Cranked power"** indicator on every screen.
2. **E-ink pages first.** Voice only on an explicit TALK press, never idle listening.
3. Skip non-essential refreshes, and keep **Wi-Fi and BLE off** (no mesh work).
4. Watch the 5 V rail if it can be read; if not, keep a **refresh-count budget** per fill. Show
   **"Crank more"** before the brown-out, not after.
5. Save the current page in **NVS** so it comes back after a brown-out and the next crank.

Fits with the existing "battery reserve: emergency pages only / crank to open" behaviour in `main/ui.c`.
