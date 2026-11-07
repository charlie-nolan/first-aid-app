#pragma once
#include <stdbool.h>
#include "cart.h"
bool voice_init(const cart_t *c);    /* loads the MultiNet model; false if voice is off or failed */
/* (Re)load the command list for section `tree` (CART_NONE = home) under context ctx.
 * Uses the cartridge's PHRS list (cart_voice_list); older cartridges fall back to phrases.txt. */
void voice_load(uint16_t tree, const cart_tags_t *ctx);
void voice_start(void);   /* mic on, start feeding audio (TALK pressed) */
void voice_stop(bool recognise);   /* TALK released: post MSG_VOICE with the result, or just cancel */
