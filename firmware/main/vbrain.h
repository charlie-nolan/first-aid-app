/* vbrain.h - the board voice's brain without the hardware: the form engine (components/vform, the same
 * screens as vr.form) and keyword search (vform_search) on the cart's FORM / SRCH / NKEY chunks, turned into
 * the ui's vs_screen_t / vs_hit_t. Pure C: voice_diy.c runs it in its worker task, sim/ runs it on a PC. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "ui.h"

typedef struct { uint8_t op; bool plain; char a[80], b[48], c[80]; } vb_op_t;   /* VF_* + what was picked */

bool vb_init(const void *rom, size_t len);        /* false: no FORM chunk */
bool vb_has_form(void), vb_has_search(void), vb_has_cards(void);
int vb_ntrees(void);
uint16_t vb_node_of(const char *key);             /* "tree/card" -> cart node (CART_NONE if not there) */
/* spoken words (reject: the speech engine's calibrated score; at reject_at or above it asks again) */
bool vb_said(const char *text, int reject, int reject_at, bool new_problem, vs_screen_t *s);
bool vb_op_of(int vf, const ui_t *u, vb_op_t *o); /* the pick / open / skip on the screen u shows */
bool vb_op(const vb_op_t *o, vs_screen_t *s);
int vb_search(const char *q, int reject, int reject_at, vs_hit_t *hits, int max);   /* -1: not heard */
