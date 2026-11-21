#pragma once
#include <stdbool.h>
#include "cart.h"
bool voice_init(const cart_t *c);    /* loads the MultiNet model; false if voice is off or failed */
/* (Re)load the command list for section `tree` (CART_NONE = home) under context ctx.
 * Uses the cartridge's PHRS list (cart_voice_list); older cartridges fall back to phrases.txt. */
void voice_load(uint16_t tree, const cart_tags_t *ctx);
void voice_start(void);   /* mic on, start feeding audio (TALK pressed) */
void voice_stop(bool recognise);   /* TALK released: post MSG_VOICE with the result, or just cancel */

/* Board voice (LIB_VOICE_DIY, voice_diy.c): where a TALK clip goes, and the form / search results. */
#include "ui.h"
enum { VT_NEW, VT_ANSWER, VT_SEARCH };          /* a new problem, an answer on the open form, a search word */
void voice_stop_to(bool recognise, int target);
bool voice_has_form(void);
bool voice_has_search(void);
void voice_form_op(int vf, const ui_t *u);      /* VF_PICK / VF_OPEN / VF_SKIP on the screen u shows */
/* copy the newest result out (MSG_VSCREEN: s; MSG_VSEARCH: hits, nhits, q) */
void voice_take(vs_screen_t *s, vs_hit_t *hits, int16_t *nhits, char *q, size_t qcap);
