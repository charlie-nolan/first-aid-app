# Fire mode: instructions stay on the e-ink after power-off (queued: after the current board tests)

Charlie, 2026-11-20: when the toggle goes to **FIRE**, the boards lose power (the 3PDT cuts the LiPo and
takes the supercaps off the device rail). The e-ink keeps its last image with no power, so the firmware
should **leave a fire-instructions screen** on it: how to use the pull-out kanthal coil, the crank, the
slide compartment and the fire button, plus the ferro rod / striker / tinder in the compartment.

Hardware recap: in FIRE the crank and supercaps feed the coil through a diode-OR. The coil glows only
with the toggle on FIRE + the slide compartment open + the fire button held.

## The timing problem

A full e-ink refresh takes ~1-2 s at ~20-40 mA; a partial ~0.3-0.5 s. The power goes as soon as the
toggle moves, so the screen must be drawn **before** that, or during a short hold-up.

## Decision (hardware thread, 2026-11-20)

- **Toggle sensing: not possible.** All 3 poles of the 3PDT are used, the Plus has no spare RTC GPIO,
  and a sense pole would switch at the same moment the rails drop.
- **Hold-up: not in v1.** It would need ~50 mF of low-ESR storage, not worth the space.

**So the only path is a menu pre-arm:** "Fire" in the hold-DOWN menu draws the fire instructions with a
full refresh, waits for the refresh to finish, then shows (as part of the same page) "Now flip the
toggle to FIRE". The page stays on the glass after the power goes. **Backup:** the same steps are
printed or engraved inside the fire compartment lid.

The page content (coil, crank, slide, fire button, ferro rod / striker / tinder) comes from the library
like every other card, so it is reviewed and cited.
