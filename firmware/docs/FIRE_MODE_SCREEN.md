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

## Options (pick with the hardware thread)

1. **Pre-arm in the menu (no hardware change):** "Fire" in the hold-DOWN menu draws the fire
   instructions and says "Now switch to FIRE". Simple and reliable; relies on the person using the menu.
2. **Fire instructions as the resting screen:** every deep sleep / low-battery shutdown leaves a page
   whose corner (or a full page in the sleep-art rotation) has the fire steps, so whatever the power
   state, the steps are on the glass. Costs screen space on every sleep.
3. **Sense the toggle early + hold-up:** a spare pole or a break-before-make contact on the 3PDT to a
   GPIO (RTC-capable, wakes from sleep), plus enough hold-up on the device rail (a few mF on 3V3, or
   leave one supercap on the device rail for ~2 s through a diode) to finish one partial refresh.
   Most robust in use; needs a hardware change and a bench check of the hold-up time.

Recommendation: **1 now** (firmware only), **3 when the hardware allows**, with 2 as the fallback if 3
can't be made reliable. The page content itself (coil, crank, slide, button, ferro rod) should come
from the library like every other card, so it is reviewed and cited.
