# Fire mode: instructions stay on the e-ink after power-off (queued: after the current board tests)

Charlie, 2026-11-20: when the toggle goes to **FIRE**, the boards lose power (the 3PDT cuts the LiPo and
takes the supercaps off the device rail). The e-ink keeps its last image with no power, so the firmware
should **leave a fire-instructions screen** on it: how to use the pull-out kanthal coil, the crank and
the fire button, and to let the coil cool before stowing it.

Hardware recap (updated 2026-11-20): the fire pocket is in the right end wall, with the **FIRE toggle
inside the pocket**; the lid only closes with the toggle on RUN, so lid + toggle are the interlock (no
lid microswitch any more, no ferro rod). In FIRE the crank and supercaps feed the coil through a
diode-OR; the coil heats only with the toggle on FIRE and the fire button held.

## The timing problem

A full e-ink refresh takes ~1-2 s at ~20-40 mA; a partial ~0.3-0.5 s. The power goes as soon as the
toggle moves, so the screen must be drawn **before** that, or during a short hold-up.

## Decision (hardware thread, 2026-11-20)

- **Toggle sensing: not possible.** All 3 poles of the 3PDT are used, the Plus has no spare RTC GPIO,
  and a sense pole would switch at the same moment the rails drop.
- **Hold-up: not in v1.** It would need ~50 mF of low-ESR storage, not worth the space.

**So the only path is a menu pre-arm** (flow set by Charlie, 2026-11-20; updated for the pocket):

1. **Fire** in the hold-DOWN menu: open the fire pocket and pull out the coil.
2. "Press NEXT and wait for the fire instructions to load." The person presses next.
3. The firmware draws the fire instructions (coil, crank, fire button, **let the coil cool before
   stowing**) with a **full refresh** and waits for the panel's BUSY line to clear.
4. Only then does the page show **"Screen ready: flip the toggle to FIRE"** in the **small font on the
   very bottom line**, revealed by a fast partial update of that one line after BUSY clears.
5. The person flips the toggle (inside the pocket); power drops; the instructions stay on the glass.

**Cool-down warning (Charlie, 2026-11-20):** "Let the coil cool before stowing" is part of the pre-drawn
fire page, **boxed and bold**, so it is on the glass the whole time the board is off. The pre-arm also
sets a flag in NVS; on the next power-up (toggle back to RUN) the first screen is a short "Coil may still
be hot: let it cool before stowing" reminder (any button dismisses it), then the flag is cleared.

The page content (coil, crank, fire button, cool before stowing) comes from the library
like every other card, so it is reviewed and cited.
