/* vbrain.c - see vbrain.h. Pure C (vform + vform_search + cJSON), shared by voice_diy.c and sim/. */
#include "vbrain.h"
#include <stdio.h>
#include <string.h>
#include "cJSON.h"
#include "vform_search.h"
#include "vform_show.h"

static const uint8_t *nkey;                 /* NK01: sorted "tree/card" -> cart node */
static fs_index_t sidx; static bool sidx_ok;
static fp_sess_t sess; static fp_screen_t scr; static bool form_ok;
static char out[16384];                     /* one SHOW frame */
#define OUT_CAP sizeof out

/* ---------- the cart's chunks ---------- */
static uint32_t rd32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
uint16_t vb_node_of(const char *key) {
    if (!nkey || !key || !key[0]) return CART_NONE;
    uint32_t lo = 0, hi = rd32(nkey + 4);
    while (lo < hi) {
        uint32_t mid = (lo + hi) / 2;
        const uint8_t *e = nkey + 8 + 12 * mid;
        int c = strcmp((const char *)nkey + rd32(e), key);
        if (!c) return (uint16_t)(e[8] | e[9] << 8);
        if (c < 0) lo = mid + 1; else hi = mid;
    }
    return CART_NONE;
}

/* ---------- a form screen (vform_show JSON) into the ui's struct ---------- */
static void cpy(char *dst, size_t cap, const cJSON *o, const char *name) {
    const cJSON *v = name ? cJSON_GetObjectItem(o, name) : o;
    dst[0] = 0;
    if (cJSON_IsString(v)) { strncpy(dst, v->valuestring, cap - 1); dst[cap - 1] = 0; }
}
static bool parse_screen(const char *js, vs_screen_t *s) {
    cJSON *r = cJSON_Parse(js);
    if (!r) return false;
    memset(s, 0, sizeof *s);
    const cJSON *v = cJSON_GetObjectItem(r, "seq"); s->seq = cJSON_IsNumber(v) ? v->valueint : 0;
    char kind[16]; cpy(kind, sizeof kind, r, "kind");
    s->kind = !strcmp(kind, "form") ? VS_FORM : !strcmp(kind, "card") ? VS_CARD : !strcmp(kind, "skip") ? VS_SKIP
            : !strcmp(kind, "again") ? VS_AGAIN : VS_START;
    cpy(s->heard, sizeof s->heard, r, "heard"); cpy(s->msg, sizeof s->msg, r, "msg");
    const cJSON *f;
    cJSON_ArrayForEach(f, cJSON_GetObjectItem(r, "fields")) {
        if (s->nf >= VS_FIELDS) break;
        vs_field_t *d = &s->f[s->nf];
        cpy(d->key, sizeof d->key, f, "key"); cpy(d->q, sizeof d->q, f, "q");
        const cJSON *c;
        cJSON_ArrayForEach(c, cJSON_GetObjectItem(f, "choices")) {
            if (d->nch >= VS_CHOICES) break;
            vs_choice_t *h = &d->ch[d->nch];
            cpy(h->label, sizeof h->label, c, "label"); cpy(h->value, sizeof h->value, c, "value"); cpy(h->node, sizeof h->node, c, "node");
            if (!h->label[0]) snprintf(h->label, sizeof h->label, "%s", h->value[0] ? h->value : h->node);
            d->nch++;
        }
        if (d->nch) s->nf++;
    }
    const cJSON *it;
    cJSON_ArrayForEach(it, cJSON_GetObjectItem(r, "cards")) {
        if (s->ncards >= VS_CARDS) break;
        vs_card_t *k = &s->cards[s->ncards++];
        cpy(k->title, sizeof k->title, it, "title"); cpy(k->node, sizeof k->node, it, "node");
        k->cart_node = vb_node_of(k->node);
    }
    const cJSON *c = cJSON_GetObjectItem(r, "card");
    if (c) { cpy(s->card.title, sizeof s->card.title, c, "title"); cpy(s->card.node, sizeof s->card.node, c, "node"); }
    s->card.cart_node = vb_node_of(s->card.node);
    cJSON_Delete(r);
    return true;
}


bool vb_init(const void *rom, size_t len) {
    size_t n = 0;
    const uint8_t *nk = fp_cart_chunk(rom, len, "NKEY", &n);
    nkey = nk && n >= 8 && !memcmp(nk, "NK01", 4) ? nk : NULL;
    const void *sr = fp_cart_chunk(rom, len, "SRCH", &n);
    sidx_ok = sr && fs_open(&sidx, sr, n);
    const void *fp = fp_cart_chunk(rom, len, "FORM", &n);
    form_ok = fp && fp_sess_init(&sess, fp, n);
    if (form_ok) { fp_screen_init(&scr, &sess); scr.form_hint = "Hold TALK and say the answers, or pick with buttons."; }
    return form_ok;
}
bool vb_has_form(void) { return form_ok; }
bool vb_has_search(void) { return sidx_ok; }
bool vb_has_cards(void) { return nkey != NULL; }
int vb_ntrees(void) { return form_ok ? sess.ntrees : 0; }
bool vb_said(const char *text, int reject, int reject_at, bool new_problem, vs_screen_t *s) {
    if (!form_ok) return false;
    if (new_problem) fp_screen_new(&scr, out, OUT_CAP);
    if (reject >= reject_at) fp_screen_again(&scr, text, reject, out, OUT_CAP);
    else fp_screen_said(&scr, text, out, OUT_CAP);
    return parse_screen(out, s);
}
bool vb_op(const vb_op_t *o, vs_screen_t *s) {
    if (!form_ok) return false;
    if (o->op == VF_PICK) fp_screen_picked(&scr, o->a, o->plain ? NULL : o->b, o->plain ? o->c : NULL, out, OUT_CAP);
    else if (o->op == VF_OPEN) fp_screen_open(&scr, o->a, out, OUT_CAP);
    else if (o->op == VF_SKIP) fp_screen_skip(&scr, out, OUT_CAP);
    else fp_screen_new(&scr, out, OUT_CAP);
    return parse_screen(out, s);
}
bool vb_op_of(int vf, const ui_t *u, vb_op_t *o) {
    const vs_screen_t *v = u->vs;
    memset(o, 0, sizeof *o); o->op = (uint8_t)vf;
    if (vf == VF_PICK && v->kind == VS_FORM && v->nf) {
        const vs_field_t *f = &v->f[u->vs_f]; const vs_choice_t *c = &f->ch[u->vs_c];
        o->plain = !c->value[0] && c->node[0];
        snprintf(o->a, sizeof o->a, "%s", f->key); snprintf(o->b, sizeof o->b, "%s", c->value); snprintf(o->c, sizeof o->c, "%s", c->node);
        return true;
    }
    if (vf == VF_OPEN && v->kind == VS_SKIP && v->ncards) { snprintf(o->a, sizeof o->a, "%s", v->cards[u->vs_card].node); return true; }
    return vf == VF_SKIP;
}
int vb_search(const char *q, int reject, int reject_at, vs_hit_t *hits, int max) {
    static fs_hit_t h[FS_MAX_HITS];
    if (reject >= reject_at || !q[0]) return -1;
    int n = sidx_ok ? fs_search(&sidx, q, h) : 0;
    if (n > max) n = max;
    for (int i = 0; i < n; i++) {
        const char *lab = strstr(h[i].title, " > "); lab = lab ? lab + 3 : h[i].title;
        snprintf(hits[i].title, sizeof hits[i].title, "%s", lab);
        hits[i].cart_node = vb_node_of(h[i].node); hits[i].life = h[i].life;
    }
    return n;
}
