/* vform_show.c - the tether's SHOW frames, built on the board: the C port of the screen logic in
 * firmware/tether/pc_bridge.py (as pinned by vr/parity.py at 3d07632). Same JSON bytes as the PC
 * (compact separators, ASCII with \uXXXX escapes), so the tether UI can draw the board's own frames
 * and the board's frames can be diffed against the PC's. */
#include "vform_show.h"
#include <stdio.h>
#include <string.h>
#ifdef ESP_PLATFORM            /* big scratch arrays: in PSRAM on the chip (internal RAM is short), plain statics on the PC */
#include "esp_attr.h"
#define FP_BIG EXT_RAM_BSS_ATTR
#else
#define FP_BIG
#endif

typedef struct { char *p; size_t cap, n; } jb_t;

static void put(jb_t *b, const char *s, size_t L) {
    for (size_t i = 0; i < L; i++) { if (b->n + 1 < b->cap) b->p[b->n] = s[i]; b->n++; }
    if (b->cap) b->p[b->n < b->cap ? b->n : b->cap - 1] = 0;
}
static void raw(jb_t *b, const char *s) { put(b, s, strlen(s)); }

static unsigned utf8(const unsigned char *s, int *len) {   /* one code point; bad bytes pass as U+FFFD */
    if (s[0] < 0x80) { *len = 1; return s[0]; }
    int n = (s[0] & 0xE0) == 0xC0 ? 2 : (s[0] & 0xF0) == 0xE0 ? 3 : (s[0] & 0xF8) == 0xF0 ? 4 : 0;
    if (!n) { *len = 1; return 0xFFFD; }
    unsigned c = s[0] & (0x7F >> n);
    for (int i = 1; i < n; i++) { if ((s[i] & 0xC0) != 0x80) { *len = 1; return 0xFFFD; } c = c << 6 | (s[i] & 0x3F); }
    *len = n;
    return c;
}

/* json.dumps(s[:maxcp]) with ensure_ascii; maxcp < 0 = whole string; s NULL = null */
static void jstr(jb_t *b, const char *s, int maxcp) {
    if (!s) { raw(b, "null"); return; }
    raw(b, "\"");
    char e[16];
    for (const unsigned char *p = (const unsigned char *)s; *p && maxcp != 0; maxcp--) {
        int L; unsigned c = utf8(p, &L); p += L;
        if (c == '"') raw(b, "\\\""); else if (c == '\\') raw(b, "\\\\");
        else if (c == '\n') raw(b, "\\n"); else if (c == '\r') raw(b, "\\r"); else if (c == '\t') raw(b, "\\t");
        else if (c == '\b') raw(b, "\\b"); else if (c == '\f') raw(b, "\\f");
        else if (c < 0x20 || (c >= 0x7F && c < 0x10000)) { snprintf(e, sizeof e, "\\u%04x", c); raw(b, e); }
        else if (c >= 0x10000) {
            c -= 0x10000;
            snprintf(e, sizeof e, "\\u%04x\\u%04x", 0xD800 + (c >> 10), 0xDC00 + (c & 0x3FF)); raw(b, e);
        } else { char ch = (char)c; put(b, &ch, 1); }
    }
    raw(b, "\"");
}
static void jint(jb_t *b, long v) { char e[24]; snprintf(e, sizeof e, "%ld", v); raw(b, e); }

#define SS(id) fp_s(&sc->s->pk, (id))

static void head(fp_screen_t *sc, jb_t *b, const char *kind) {
    sc->seq++;
    raw(b, "{\"v\":1,\"seq\":"); jint(b, sc->seq);
    raw(b, ",\"kind\":"); jstr(b, kind, -1);
    raw(b, ",\"heard\":"); jstr(b, sc->heard, 100);
}

/* vr.live._title + the bridge's node string: "<Tree title> > <card label>", node "<name>/<card>" */
static void title(fp_screen_t *sc, int t, fp_card card, jb_t *b, fp_str *cites, int *ncites) {
    fp_sess_t *s = sc->s;
    char buf[256];
    const char *key = card >= 0 ? SS(fp_node_key(s, t, card)) : SS(FP_EXT_SID(card));
    int tt = t, nn = card;
    if (ncites) *ncites = 0;
    if (card < 0) {
        const char *sl = strchr(key, '/');
        if (sl) {
            size_t L = (size_t)(sl - key) < sizeof buf - 1 ? (size_t)(sl - key) : sizeof buf - 1;
            memcpy(buf, key, L); buf[L] = 0;
            tt = fp_tree_by_name(s, buf);
            if (tt < 0) { jstr(b, sl + 1, -1); return; }  /* tree not loaded: the bare card name */
            key = sl + 1;
            nn = fp_node_by_key(s, tt, key);
        } else nn = -1;
    }
    const char *tl = SS(s->trees[tt].title);
    const char *nl = nn >= 0 && fp_node_label(s, tt, nn) != FP_NONE ? SS(fp_node_label(s, tt, nn)) : key;
    snprintf(buf, sizeof buf, "%s > %s", tl, nl);
    jstr(b, buf, -1);
    if (ncites && nn >= 0) *ncites = fp_node_cites(s, tt, nn, cites, 6);
}

/* vr.form.node_id: "tree/card"; a card in another tree keeps its own id */
static void node_str(fp_screen_t *sc, int t, fp_card card, jb_t *b, int maxcp, const char *to) {
    char buf[256];
    const char *key = to ? to : card >= 0 ? SS(fp_node_key(sc->s, t, card)) : SS(FP_EXT_SID(card));
    bool local = to ? fp_node_by_key(sc->s, t, to) >= 0 : card >= 0;
    if (!local && strchr(key, '/')) { jstr(b, key, maxcp); return; }
    snprintf(buf, sizeof buf, "%s/%s", SS(sc->s->trees[t].name), key);
    jstr(b, buf, maxcp);
}

static void refs(fp_screen_t *sc, jb_t *b, const fp_ref_t *r, int n) {
    raw(b, "[");
    for (int i = 0; i < n; i++) {
        if (i) raw(b, ",");
        raw(b, "{\"title\":"); title(sc, r[i].tree, r[i].card, b, NULL, NULL);
        raw(b, ",\"node\":"); node_str(sc, r[i].tree, r[i].card, b, -1, NULL);
        raw(b, "}");
    }
    raw(b, "]");
}

static int form_screen(fp_screen_t *sc, jb_t *b, const char *msg) {
    fp_sess_t *s = sc->s;
    fp_field_t f[FP_MAX_FIELDS];
    int nf = fp_open_fields(s, f, FP_MAX_FIELDS), shown = 0;
    head(sc, b, "form");
    raw(b, ",\"fields\":[");
    for (int i = 0; i < nf && shown < 6; i++) {
        int nch = 0;
        if (f[i].key == FP_TOPIC) nch = s->ncands;
        else {
            fp_tile_t ts[32];
            int nt = fp_tiles(s, f[i].tree, f[i].node, ts, 32);
            for (int k = 0; k < nt; k++)
                if ((ts[k].value != FP_NONE && *SS(ts[k].value)) ||
                    (f[i].when_slot == FP_NONE && ts[k].to != FP_NONE && *SS(ts[k].to))) nch++;
        }
        if (!nch) continue;
        if (shown++) raw(b, ",");
        raw(b, "{\"key\":"); jstr(b, f[i].key == FP_TOPIC ? "@topic" : SS(f[i].key), -1);
        raw(b, ",\"q\":");
        if (f[i].key == FP_TOPIC) jstr(b, "Which is it?", 94);
        else jstr(b, f[i].question != FP_NONE ? SS(f[i].question) : SS(f[i].key), 94);
        raw(b, ",\"choices\":[");
        int k12 = 0;
        if (f[i].key == FP_TOPIC) {
            for (int c = 0; c < s->ncands && k12 < 12; c++, k12++) {
                if (k12) raw(b, ",");
                const fp_tree_t *t = &s->trees[s->cands[c].tree];
                raw(b, "{\"label\":"); jstr(b, SS(t->title), 46);
                raw(b, ",\"value\":"); jstr(b, SS(t->name), 46); raw(b, "}");
            }
        } else {
            fp_tile_t ts[32];
            int nt = fp_tiles(s, f[i].tree, f[i].node, ts, 32);
            /* f.trees[0]: the first candidate (in candidate order) that has this field */
            int t0 = f[i].tree;
            for (int c = 0; c < s->ncands; c++) if (f[i].trees >> c & 1) { t0 = s->cands[c].tree; break; }
            for (int k = 0; k < nt && k12 < 12; k++) {
                bool hv = ts[k].value != FP_NONE && *SS(ts[k].value), ht = ts[k].to != FP_NONE && *SS(ts[k].to);
                if (f[i].when_slot != FP_NONE) ht = false;   /* look-ahead fields: answers only */
                if (!hv && !ht) continue;
                if (k12++) raw(b, ",");
                const char *lab = ts[k].label != FP_NONE && *SS(ts[k].label) ? SS(ts[k].label) : hv ? SS(ts[k].value) : SS(ts[k].to);
                raw(b, "{\"label\":"); jstr(b, lab, 46);
                if (hv) { raw(b, ",\"value\":"); jstr(b, SS(ts[k].value), 46); }
                else { raw(b, ",\"value\":null,\"node\":"); node_str(sc, t0, 0, b, 78, SS(ts[k].to)); }
                raw(b, "}");
            }
        }
        raw(b, "],\"after\":");
        if (f[i].when_slot == FP_NONE) raw(b, "null");
        else { raw(b, "["); jstr(b, SS(f[i].when_slot), -1); raw(b, ","); jstr(b, SS(f[i].when_val), -1); raw(b, "]"); }
        raw(b, "}");
    }
    raw(b, "],\"top\":");
    fp_ref_t r[3];
    int nr = fp_ranked_cards(s, 3, r, 3);
    refs(sc, b, r, nr);
    raw(b, ",\"msg\":"); jstr(b, msg[0] || !sc->form_hint ? msg : sc->form_hint, -1);
    raw(b, "}");
    return (int)b->n;
}

static int card_screen(fp_screen_t *sc, jb_t *b, int t, fp_card card) {
    fp_str cites[6]; int nc = 0;
    head(sc, b, "card");
    raw(b, ",\"card\":{\"title\":"); title(sc, t, card, b, cites, &nc);
    raw(b, ",\"cites\":[");
    for (int i = 0; i < nc; i++) { if (i) raw(b, ","); jstr(b, SS(cites[i]), -1); }
    raw(b, "],\"node\":"); node_str(sc, t, card, b, -1, NULL);
    raw(b, "}}");
    sc->open = false;
    fp_reset(sc->s);
    return (int)b->n;
}

static int start(fp_screen_t *sc, jb_t *b, const char *msg) {
    head(sc, b, "start");
    if (msg) { raw(b, ",\"msg\":"); jstr(b, msg, -1); }
    raw(b, "}");
    return (int)b->n;
}

static int result(fp_screen_t *sc, jb_t *b, fp_result_t r) {
    if (r.kind == FP_OPEN) return card_screen(sc, b, r.tree, r.card);
    if (r.kind == FP_SKIP) {
        fp_ref_t refs12[12];
        int n = fp_ranked_cards(sc->s, 12, refs12, 12);
        head(sc, b, "skip");
        raw(b, ",\"cards\":"); refs(sc, b, refs12, n < 6 ? n : 6);
        raw(b, "}");
        return (int)b->n;
    }
    if (r.kind == FP_TABS) {                         /* the first (most urgent) tab; a life tab opens its card */
        static FP_BIG fp_tab_t tabs[5];
        fp_split_tabs(sc->s, tabs, 5);
        fp_keep_tab(sc->s, &tabs[0]);
        fp_cand_t *lead = &sc->s->cands[0];
        if (fp_tab_life(sc->s, lead)) return card_screen(sc, b, lead->tree, lead->card);
        return form_screen(sc, b, "Several problems: showing the first");
    }
    return form_screen(sc, b, "");
}

void fp_screen_init(fp_screen_t *sc, fp_sess_t *s) { memset(sc, 0, sizeof *sc); sc->s = s; }

int fp_screen_said(fp_screen_t *sc, const char *text, char *out, size_t cap) {
    const char *h[1] = {text};
    return fp_screen_said_n(sc, h, 1, out, cap);
}
/* vr.live's loop: keep_trees, first_sentence and rescue see every guess (a lower guess may add a
 * life-threat blank the top one missed: fill_nbest); the answer and the commands use the top one. */
int fp_screen_said_n(fp_screen_t *sc, const char *const *hyps, int nh, char *out, size_t cap) {
    const char *text = nh > 0 ? hyps[0] : "";
    jb_t b = {out, cap, 0};
    snprintf(sc->heard, sizeof sc->heard, "%s", text);
    char w[16]; size_t n = 0;                         /* text.strip().lower(), enough to compare */
    const char *p = text;
    while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r' || *p == '\f' || *p == '\v') p++;
    size_t L = strlen(p);
    while (L && (p[L - 1] == ' ' || p[L - 1] == '\t' || p[L - 1] == '\n' || p[L - 1] == '\r' || p[L - 1] == '\f' || p[L - 1] == '\v')) L--;
    for (; n < L && n < sizeof w - 1; n++) w[n] = (char)(p[n] >= 'A' && p[n] <= 'Z' ? p[n] + 32 : p[n]);
    w[n] = 0;
    bool fits = L < sizeof w;
    if (!L || (fits && (!strcmp(w, "new") || !strcmp(w, "home") || !strcmp(w, "back")))) {
        sc->open = false; fp_reset(sc->s);
        return start(sc, &b, NULL);
    }
    if (fits && !strcmp(w, "multi")) { sc->multi = true; return start(sc, &b, "Next sentence = several problems"); }
    fp_result_t r;
    if (!sc->open) {
        if (!fp_keep_trees(sc->s, hyps, nh, 3)) {
            sc->open = false;
            return start(sc, &b, "No blanks heard in any tree. Hold TALK and say what happened.");
        }
        sc->open = true;
        r = fp_first_sentence(sc->s, hyps, nh, sc->multi);
        sc->multi = false;
    } else {
        /* a life threat / new problem named mid-form */
        if (!fp_rescue(sc->s, hyps, nh, &r)) r = fp_answer(sc->s, text);
    }
    return result(sc, &b, r);
}

int fp_screen_picked(fp_screen_t *sc, const char *field, const char *value, const char *node, char *out, size_t cap) {
    jb_t b = {out, cap, 0};
    if (!sc->open) return start(sc, &b, "Hold TALK and say what happened.");
    if (!value && node && *node) return result(sc, &b, fp_pick(sc->s, field, NULL, node));
    return result(sc, &b, fp_pick(sc->s, field, value, NULL));
}

int fp_screen_open(fp_screen_t *sc, const char *node, char *out, size_t cap) {
    jb_t b = {out, cap, 0};
    char name[128];
    const char *sl = strchr(node, '/');
    size_t L = sl ? (size_t)(sl - node) : strlen(node);
    if (L >= sizeof name) L = sizeof name - 1;
    memcpy(name, node, L); name[L] = 0;
    int t = fp_tree_by_name(sc->s, name);
    int nd = t >= 0 && sl ? fp_node_by_key(sc->s, t, sl + 1) : -1;
    if (t < 0) return start(sc, &b, "Hold TALK and say what happened.");
    if (nd < 0) {
        int ext = sl ? fp_find_str(&sc->s->pk, sl + 1) : -1;
        if (ext < 0) return start(sc, &b, "Hold TALK and say what happened.");
        return card_screen(sc, &b, t, FP_EXT((fp_str)ext));
    }
    return card_screen(sc, &b, t, nd);
}

int fp_screen_new(fp_screen_t *sc, char *out, size_t cap) {
    jb_t b = {out, cap, 0};
    sc->open = false; sc->multi = false; sc->heard[0] = 0; fp_reset(sc->s);
    return start(sc, &b, NULL);
}

int fp_screen_skip(fp_screen_t *sc, char *out, size_t cap) {
    jb_t b = {out, cap, 0};
    if (!sc->open) return start(sc, &b, "Hold TALK and say what happened.");
    return result(sc, &b, (fp_result_t){FP_SKIP, 0, 0});
}

int fp_screen_again(fp_screen_t *sc, const char *text, int reject, char *out, size_t cap) {
    jb_t b = {out, cap, 0};
    char m[96];
    snprintf(sc->heard, sizeof sc->heard, "%s", text);
    if (sc->open && sc->form_hint)      /* tether: a form is open, keep it on screen and just say so */
        return form_screen(sc, &b, "Didn't catch that. Hold TALK, say it again, or pick.");
    snprintf(m, sizeof m, "Didn't catch that (reject %d). Hold TALK and say it again.", reject);
    head(sc, &b, "again");
    raw(&b, ",\"msg\":"); jstr(&b, m, -1);
    raw(&b, "}");
    return (int)b.n;
}
