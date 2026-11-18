/* vform_show.h - SHOW frames (the tether's JSON screens) built on the board from vform.
 * Same bytes as firmware/tether/pc_bridge.py / vr/parity.py at 5fa165f (rules in that docstring). */
#pragma once
#include "vform.h"

typedef struct {
    fp_sess_t *s;
    int seq;
    bool open;                /* a problem is open (the Python's cands is not None) */
    bool multi;               /* next sentence = several problems */
    char heard[FP_HEARD];
    const char *form_hint;    /* tether: msg on form frames that have none (NULL = vr.parity frames) */
} fp_screen_t;

void fp_screen_init(fp_screen_t *sc, fp_sess_t *s);
/* Each returns the frame length (it may be more than cap: then out holds a cut, NUL-ended copy). */
int fp_screen_said(fp_screen_t *sc, const char *text, char *out, size_t cap);
/* A tapped choice: value NULL + node = a plain tile (node id sent back unchanged). */
int fp_screen_picked(fp_screen_t *sc, const char *field, const char *value, const char *node, char *out, size_t cap);
int fp_screen_skip(fp_screen_t *sc, char *out, size_t cap);
int fp_screen_open(fp_screen_t *sc, const char *node, char *out, size_t cap);
/* Speech reject: the bridge's "again" frame. */
int fp_screen_again(fp_screen_t *sc, const char *text, int reject, char *out, size_t cap);
int fp_screen_new(fp_screen_t *sc, char *out, size_t cap);
