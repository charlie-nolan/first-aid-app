# README screen pictures

Every picture here is the firmware's own `ui.c` and C form engine (`components/vform`, `main/vbrain.c`)
running on the PC through `firmware/sim/sim.c -DSIM_VOICE`, at the panel's real 250x122 (shown 2x).
Voice input is **typed text** standing in for what the recognizer heard; no audio and no voice is involved.

Content is public only: the ATP 4-02.11 bleeding, CPR and choking trees in `pipeline/plb/content/trees`
(US Government work) and the small demo word lists in [`public_slots/`](public_slots/), written for these
pictures (they are not the personal slot maps and hold no manual text). The personal Aerie and Red Cross
library never appears here.

| Picture | Shows |
|---|---|
| `2026-11-23_screens-buttons-home-to-card_run1_test1` | Buttons only: start screen, folder and topic tiles, a cited card |
| `2026-11-23_screens-voice-life-card-first-sentence_run1_test2` | A life threat opens its card on the first sentence |
| `2026-11-23_screens-voice-form-screen2_run1_test3` | Screen 2, the fill-in-the-blanks form, then the card |
| `2026-11-23_screens-voice-skip-likely-cards_run1_test4` | SKIP (hold DOWN): ranked likely cards |
| `2026-11-23_screens-keyword-search-grid_run1_test5` | Search by word: the 4 x 2 tile grid |
| `2026-11-23_screens-sleep-art_run1_test6` | Sleep screen |

Rebuild: `FW=<checkout with the board-voice firmware> VR=<voice_router with vr/live.py> ./make_screens.sh`.
