#include "cart.h"
#include <string.h>

static inline uint16_t rd16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }
static inline uint32_t rd32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }

static uint32_t crc32(const uint8_t *p, uint32_t n) {
    uint32_t c = 0xFFFFFFFFu;
    while (n--) { c ^= *p++; for (int k = 0; k < 8; k++) c = (c >> 1) ^ (0xEDB88320u & -(c & 1)); }
    return ~c;
}

int cart_open(cart_t *c, const void *rom, uint32_t len, int check_crc) {
    const uint8_t *b = rom;
    static const char tags[] = "SYMSHUFFTIDXTEXTPASGNODENPASCHOITREECMDSSRCSPICXPICDSTRS";
    const uint8_t **slot[] = { &c->syms, &c->huff, &c->tidx, &c->text, &c->pasg, &c->node,
                               &c->npas, &c->choi, &c->tree, &c->cmds, &c->srcs, &c->picx, &c->picd, &c->strs };
    uint32_t sz[14] = {0};
    memset(c, 0, sizeof *c);
    if (len < 28 || memcmp(b, "CRT1", 4) || rd16(b + 4) != 1 || rd32(b + 8) > len) return CART_EMAGIC;
    if (check_crc && crc32(b + 16, rd32(b + 8) - 16) != rd32(b + 12)) return CART_ECRC;
    c->base = b; c->size = rd32(b + 8);
    c->home_tree = rd16(b + 16); c->sleep_picture = rd16(b + 18);
    c->n_nodes = rd16(b + 20); c->n_texts = rd16(b + 22);
    for (unsigned i = 0, n = rd16(b + 6); i < n; i++) {
        const uint8_t *d = b + 28 + 12 * i;
        for (unsigned t = 0; t < 14; t++)
            if (!memcmp(d, tags + 4 * t, 4)) { *slot[t] = b + rd32(d + 4); sz[t] = rd32(d + 8); }
    }
    for (unsigned i = 0, n = rd16(b + 6); i < n; i++)          /* optional chunks */
    {
        const uint8_t *d = b + 28 + 12 * i, *p = b + rd32(d + 4);
        if (!memcmp(d, "FONT", 4)) c->font = p;
        else if (!memcmp(d, "TAGP", 4)) c->tagp = p;
        else if (!memcmp(d, "TAGI", 4)) { c->tagi = p; c->n_tagi = rd32(d + 8) / 4; }
        else if (!memcmp(d, "TAGN", 4)) c->tagn = p;
        else if (!memcmp(d, "PHRS", 4)) { c->phrs = p; c->n_phrs = rd32(d + 8) / 6; }
    }
    for (unsigned t = 0; t < 14; t++) if (!*slot[t]) return CART_ECHUNK;
    c->n_syms = sz[0] / 3; c->n_strs = sz[13] / 4; c->build_id = rd32(b + 24);
    c->n_cmds = sz[9] / 2; c->n_pics = sz[11] / 4 - 1; c->n_passages = sz[4] / 6; c->n_trees = sz[8] / 8;
    return CART_OK;
}

const cart_node_t *cart_node(const cart_t *c, uint16_t id) {
    return id < c->n_nodes ? (const cart_node_t *)(c->node + 16u * id) : 0;
}
cart_passage_t cart_node_passage(const cart_t *c, const cart_node_t *n, unsigned i) {
    const uint8_t *p = c->pasg + 6u * rd16(c->npas + 2u * (n->first_passage + i));
    return (cart_passage_t){ rd16(p), rd16(p + 2), rd16(p + 4) };
}
cart_choice_t cart_node_choice(const cart_t *c, const cart_node_t *n, unsigned i) {
    const uint8_t *p = c->choi + 6u * (n->first_choice + i);
    return (cart_choice_t){ rd16(p), rd16(p + 2), p[4] };
}
cart_tree_t cart_tree(const cart_t *c, uint16_t id) {
    const uint8_t *p = c->tree + 8u * id;
    return (cart_tree_t){ rd16(p), rd16(p + 2), rd16(p + 4), (uint8_t)(p[6] & 1), (uint8_t)(p[6] >> 1 & 1) };
}
int cart_node_unreviewed(const cart_t *c, uint16_t node) {
    const cart_node_t *n = cart_node(c, node);
    return n && (n->flags & CART_NODE_UNREVIEWED) != 0;
}
uint16_t cart_cmd_node(const cart_t *c, uint16_t cmd) { return cmd < c->n_cmds ? rd16(c->cmds + 2u * cmd) : CART_NONE; }
uint16_t cart_source_title(const cart_t *c, uint16_t s) { return s == CART_NONE ? CART_NONE : rd16(c->srcs + 2u * s); }
uint16_t cart_string(const cart_t *c, const char *name) {
    char buf[32];
    for (unsigned i = 0; i < c->n_strs; i++) {
        cart_text(c, rd16(c->strs + 4u * i), 0, buf, sizeof buf);
        if (!strcmp(buf, name)) return rd16(c->strs + 4u * i + 2);
    }
    return CART_NONE;
}

/* ---- text ----
 * Each text is a run of canonical Huffman codes. The Huffman index is the symbol
 * id; symbol i is a 3-byte entry of two 12-bit fields: (0xFFF, char) for a
 * character, else (left, right) symbols, expanded depth-first with a small stack. */
size_t cart_text(const cart_t *c, uint16_t id, size_t skip, char *out, size_t cap) {
    if (id >= c->n_texts) { if (cap) *out = 0; return 0; }
    uint32_t bit = rd32(c->tidx + 4u * id), end = rd32(c->tidx + 4u * id + 4);
    const uint8_t *cnt = c->huff;
    uint16_t stack[64];
    size_t n = 0, w = 0;
    while (bit < end) {
        unsigned code = 0, first = 0, index = 0;
        for (int l = 1; l <= 15; l++) {
            code |= (c->text[bit >> 3] >> (7 - (bit & 7))) & 1; bit++;
            unsigned k = rd16(cnt + 2 * l);
            if (code - first < k) break;
            index += k; first = (first + k) << 1; code <<= 1;
        }
        int sp = 0; stack[sp++] = (uint16_t)(index + code - first);
        while (sp) {
            const uint8_t *e = c->syms + 3u * stack[--sp];
            unsigned a = e[0] | (e[1] & 0x0F) << 8, b = e[1] >> 4 | e[2] << 4;
            if (a == 0xFFF) { if (n++ >= skip && w + 1 < cap) out[w++] = (char)b; }
            else { stack[sp++] = (uint16_t)b; stack[sp++] = (uint16_t)a; }
        }
    }
    if (cap) out[w] = 0;
    return n;
}

/* ---- pictures ---- */
int cart_pic(const cart_t *c, uint16_t id, cart_pic_t *p) {
    if (id >= c->n_pics) return -1;
    uint32_t o = rd32(c->picx + 4u * id), e = rd32(c->picx + 4u * id + 4);
    const uint8_t *h = c->picd + o;
    p->w = rd16(h); p->h = rd16(h + 2); p->codec = h[4]; p->pan = h[5];
    p->caption = rd16(h + 6); p->citation = rd16(h + 8);
    p->data = h + 10; p->len = e - o - 10;
    return 0;
}

typedef struct { const uint8_t *p, *end; uint32_t range, code; } rc_t;
static inline uint8_t rc_byte(rc_t *r) { return r->p < r->end ? *r->p++ : 0; }
static inline int rc_bit(rc_t *r, uint16_t *prob) {        /* LZMA bit decoder */
    uint32_t bound = (r->range >> 11) * *prob; int b;
    if (r->code < bound) { r->range = bound; *prob += (2048 - *prob) >> 5; b = 0; }
    else { r->code -= bound; r->range -= bound; *prob -= *prob >> 5; b = 1; }
    if (r->range < (1u << 24)) { r->range <<= 8; r->code = (r->code << 8) | rc_byte(r); }
    return b;
}

#define PIC_MAX_W 768
int cart_pic_decode(const cart_pic_t *p, cart_row_cb cb, void *user) {
    int bpr = (p->w + 7) / 8;
    if (p->w > PIC_MAX_W) return -1;
    uint8_t row[PIC_MAX_W / 8];
    const uint8_t *s = p->data, *end = p->data + p->len;
    if (p->codec == 0) {
        for (int y = 0; y < p->h; y++) cb(user, y, s + y * bpr, bpr);
    } else if (p->codec == 1) {                               /* PackBits over the row stream */
        int y = 0, x = 0;
        while (s < end && y < p->h) {
            int8_t n = (int8_t)*s++; int run = n >= 0 ? n + 1 : 1 - n;
            for (int k = 0; k < run; k++) {
                row[x++] = n >= 0 ? s[k] : *s;
                if (x == bpr) { cb(user, y++, row, bpr); x = 0; }
            }
            s += n >= 0 ? run : 1;
        }
    } else if (p->codec == 2) {                               /* 10-pixel context + range coder */
        uint16_t prob[1024];
        uint8_t l[3][PIC_MAX_W + 4];                          /* rows y-2, y-1, y with 2 px margins */
        for (int i = 0; i < 1024; i++) prob[i] = 1024;
        memset(l, 0, sizeof l);
        rc_t r = { s, end, 0xFFFFFFFFu, 0 };
        for (int i = 0; i < 5; i++) r.code = (r.code << 8) | rc_byte(&r);
        uint8_t *r2 = l[0], *r1 = l[1], *r0 = l[2];
        for (int y = 0; y < p->h; y++) {
            memset(row, 0, bpr);
            for (int x = 0; x < p->w; x++) {
                int X = x + 2;
                int ctx = r2[X-1] << 9 | r2[X] << 8 | r2[X+1] << 7 | r1[X-2] << 6 | r1[X-1] << 5 |
                          r1[X] << 4 | r1[X+1] << 3 | r1[X+2] << 2 | r0[X-2] << 1 | r0[X-1];
                int b = rc_bit(&r, &prob[ctx]);
                r0[X] = (uint8_t)b;
                if (b) row[x >> 3] |= 0x80 >> (x & 7);
            }
            cb(user, y, row, bpr);
            uint8_t *t = r2; r2 = r1; r1 = r0; r0 = t; memset(r0, 0, PIC_MAX_W + 4);
        }
    } else return -1;
    return 0;
}

/* ---- fonts and page layout ----
 * FONT: u8 n_faces, n_sizes, n_families, pad; u32 face_off[n_faces]; char name[n_families][16].
 * face: u8 px, line_h, ascent, flags, first_char, n_glyphs; u16 n_kern, space_adv16, pad;
 * 8-byte glyphs {u16 bitmap_off, u8 w, h, i8 x_off, y_off, u16 adv16}; 4-byte kern pairs
 * {u8 left, right, i16 value16} sorted; then bitmaps. Positions are 12.4 fixed point and each
 * glyph's origin is snapped to the nearest pixel, so spacing never drifts. */
static const uint8_t *face_of(const cart_t *c, unsigned style, int bold) {
    if (!c->font) return 0;
    unsigned i = style * 2 + (bold ? 1 : 0);
    return i < c->font[0] ? c->font + rd32(c->font + 4 + 4 * i) : 0;
}
#define FACE_HDR 12
static const uint8_t *glyph_of(const uint8_t *f, unsigned ch) {
    if (ch == CART_TXT_BULLET) ch = 127;
    if (ch < f[4] || ch >= (unsigned)f[4] + f[5]) ch = '?';
    return f + FACE_HDR + 8 * (ch - f[4]);
}
static int adv_of(const uint8_t *f, unsigned ch) { return rd16(glyph_of(f, ch) + 6); }
static int kern_of(const uint8_t *f, unsigned a, unsigned b) {
    const uint8_t *k = f + FACE_HDR + 8 * f[5];
    int lo = 0, hi = rd16(f + 6) - 1; unsigned key = a << 8 | b;
    while (lo <= hi) {
        int mid = (lo + hi) / 2; unsigned m = k[4 * mid] << 8 | k[4 * mid + 1];
        if (m == key) return (int16_t)rd16(k + 4 * mid + 2);
        if (m < key) lo = mid + 1; else hi = mid - 1;
    }
    return 0;
}
static void draw_glyph(cart_fb_t *fb, const uint8_t *f, unsigned ch, int x, int base) {
    const uint8_t *g = glyph_of(f, ch), *bm = f + FACE_HDR + 8 * f[5] + 4 * rd16(f + 6) + rd16(g);
    int w = g[2], h = g[3], xo = (int8_t)g[4], yo = (int8_t)g[5];
    for (int r = 0, k = 0; r < h; r++)
        for (int col = 0; col < w; col++, k++)
            if (bm[k >> 3] & (0x80 >> (k & 7))) {
                int px = x + xo + col, py = base + yo + r;
                if (px >= 0 && py >= 0 && px < fb->w && py < fb->h) fb->bits[py * fb->stride + (px >> 3)] |= 0x80 >> (px & 7);
            }
}
/* Draw s[0..n) with its pen starting at x16 (1/16 px); returns the pen after it. */
static int draw_run(cart_fb_t *fb, const uint8_t *f, const char *s, size_t n, int x16, int base) {
    for (size_t i = 0; i < n; i++) {
        unsigned ch = (uint8_t)s[i];
        if (fb) draw_glyph(fb, f, ch, (x16 + 8) >> 4, base);
        x16 += adv_of(f, ch) + (i + 1 < n ? kern_of(f, ch, (uint8_t)s[i + 1]) : 0);
    }
    return x16;
}
unsigned cart_font_sizes(const cart_t *c) { return c->font ? c->font[1] : 0; }
unsigned cart_font_families(const cart_t *c) { return c->font ? c->font[2] : 0; }
const char *cart_font_family_name(const cart_t *c, unsigned fam) {
    return fam < cart_font_families(c) ? (const char *)c->font + 4 + 4 * c->font[0] + 16 * fam : "";
}
int cart_font_line_height(const cart_t *c, unsigned style) { const uint8_t *f = face_of(c, style, 0); return f ? f[1] : 0; }
int cart_draw_str(const cart_t *c, unsigned style, int bold, cart_fb_t *fb, int x, int y, const char *s) {
    const uint8_t *f = face_of(c, style, bold);
    if (!f) return 0;
    return ((draw_run(fb, f, s, strlen(s), x << 4, y) + 8) >> 4) - x;
}
int cart_page(const cart_t *c, uint16_t id, unsigned style, int page, cart_fb_t *fb,
              int x, int y, int w, int h, char *scratch, size_t scratch_len) {
    const uint8_t *fr = face_of(c, style, 0), *fbold = face_of(c, style, 1);
    if (!fr || scratch_len < 2) return 0;
    if (!fbold) fbold = fr;
    size_t n = cart_text(c, id, 0, scratch, scratch_len);
    if (n >= scratch_len) n = scratch_len - 1;
    const int lh = fr[1], asc = fr[2], gap = lh / 3;
    int pg = 0, cy = 0;
    const char *s = scratch, *end = scratch + n;
    while (s < end) {
        const char *le = s;
        while (le < end && *le != '\n') le++;
        const uint8_t *f = fr; int indent16 = 0, bullet = 0;
        if (s < le && *s == CART_TXT_HEADING) { f = fbold; s++; }
        else if (s < le && *s == CART_TXT_BULLET) { bullet = 1; s++; indent16 = adv_of(f, CART_TXT_BULLET) + adv_of(f, ' '); }
        int first = 1;
        do {
            const int avail16 = (w << 4) - indent16;
            const char *p = s, *brk = 0, *lend;
            int lw = 0;
            while (p < le) {                                     /* greedy fill, remember last space */
                int a = adv_of(f, (uint8_t)*p) + (p > s ? kern_of(f, (uint8_t)p[-1], (uint8_t)*p) : 0);
                if (lw + a > avail16) break;
                lw += a; p++;
                if (p < le && *p == ' ') brk = p;
            }
            lend = p >= le ? le : brk ? brk : (p > s ? p : s + 1);   /* over-long word: hard break */
            if (cy + lh > h) { pg++; cy = 0; }
            if (fb && pg == page) {
                int base = y + cy + asc;
                if (bullet && first) draw_glyph(fb, f, CART_TXT_BULLET, x, base);
                draw_run(fb, f, s, (size_t)(lend - s), (x << 4) + indent16, base);
            }
            cy += lh; first = 0;
            s = lend;
            while (s < le && *s == ' ') s++;
        } while (s < le);
        if (cy + gap <= h) cy += gap;
        s = le + 1;
    }
    return pg + 1;
}
/* ---- context and voice lists ---- */
cart_tags_t cart_node_tags(const cart_t *c, uint16_t node) {
    cart_tags_t t = {{0, 0, 0}};                         /* untagged = global */
    if (!c->tagp || !c->tagi) return t;
    uint32_t lo = 0, hi = c->n_tagi;                    /* binary search the tagged nodes */
    while (lo < hi) {
        uint32_t mid = (lo + hi) / 2, e = rd32(c->tagi + 4 * mid);
        if ((e >> 16) == node) {
            for (int i = 0; i < 3; i++) t.w[i] = rd32(c->tagp + 12u * (e & 0xFFFF) + 4 * i);
            return t;
        }
        if ((e >> 16) < node) lo = mid + 1; else hi = mid;
    }
    return t;
}
unsigned cart_ctx_dims(const cart_t *c) { return c->tagn ? c->tagn[0] : 0; }
int cart_ctx_dim(const cart_t *c, unsigned dim, uint16_t *name_text, unsigned *shift, unsigned *width) {
    if (dim >= cart_ctx_dims(c)) return -1;
    const uint8_t *d = c->tagn + 4 + 4 * dim;
    if (name_text) *name_text = rd16(d);
    if (shift) *shift = d[2];
    if (width) *width = d[3];
    return 0;
}
void cart_ctx_clear_dim(const cart_t *c, cart_tags_t *ctx, unsigned dim) {
    unsigned s, w;
    if (cart_ctx_dim(c, dim, 0, &s, &w)) return;
    for (unsigned b = s; b < s + w && b < 96; b++) ctx->w[b >> 5] &= ~(1u << (b & 31));
}
uint16_t cart_ctx_label(const cart_t *c, unsigned bit) {
    return c->tagn && bit < 96 ? rd16(c->tagn + 4 + 4 * c->tagn[0] + 2 * bit) : CART_NONE;
}
static int any_in(const cart_tags_t *t, unsigned s, unsigned w) {
    for (unsigned b = s; b < s + w && b < 96; b++) if (t->w[b >> 5] >> (b & 31) & 1) return 1;
    return 0;
}
static int overlap(const cart_tags_t *a, const cart_tags_t *b, unsigned s, unsigned w) {
    for (unsigned k = s; k < s + w && k < 96; k++) if (a->w[k >> 5] & b->w[k >> 5] & (1u << (k & 31))) return 1;
    return 0;
}
int cart_node_visible(const cart_t *c, uint16_t node, const cart_tags_t *ctx) {
    const cart_node_t *n = cart_node(c, node);
    if (!n) return 0;
    if (n->flags & CART_NODE_EMERGENCY) return 1;            /* never filter life-threat pages */
    cart_tags_t t = cart_node_tags(c, node);
    unsigned s, w;
    for (unsigned d = 0; cart_ctx_dim(c, d, 0, &s, &w) == 0; d++)
        if (any_in(&t, s, w) && any_in(ctx, s, w) && !overlap(&t, ctx, s, w)) return 0;
    return 1;
}
uint16_t cart_phrase_count(const cart_t *c) { return c->n_phrs; }
cart_phrase_t cart_phrase(const cart_t *c, uint16_t id) {
    if (id >= c->n_phrs) return (cart_phrase_t){ CART_NONE, CART_NONE, 0 };
    const uint8_t *p = c->phrs + 6u * id;
    return (cart_phrase_t){ rd16(p), rd16(p + 2), p[4] };
}
int cart_voice_list(const cart_t *c, uint16_t tree, const cart_tags_t *ctx, uint16_t *ids, int cap) {
    int n = 0;
    for (int rank = 1; rank <= 4 && n < cap; rank++)
        for (uint16_t i = 0; i < c->n_phrs && n < cap; i++) {
            cart_phrase_t p = cart_phrase(c, i);
            const cart_node_t *nd = cart_node(c, p.node);
            if (p.text == CART_NONE || !nd || !cart_node_visible(c, p.node, ctx)) continue;
            int r = (p.flags & CART_PHRASE_ALWAYS) ? 1          /* 911 / emergency entries */
                  : (p.flags & CART_PHRASE_ENTRY) ? 2           /* jump to any section */
                  : (tree != CART_NONE && nd->tree == tree) ? 3 /* this section, in detail */
                  : 4;                                          /* fill the rest of the list */
            if (r != rank) continue;
            int dup = 0;                                        /* same words twice: keep the first */
            for (int k = 0; k < n && !dup; k++) dup = cart_phrase(c, ids[k]).text == p.text;
            if (!dup) ids[n++] = i;
        }
    return n;
}
