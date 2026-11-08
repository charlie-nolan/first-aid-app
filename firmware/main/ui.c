/* ui.c - see ui.h. Screen layout (250 x 122, landscape):
 *
 *   header  window number + node label ............ page/pages  battery
 *   body    passage text (cart_page), a picture, or a list
 *   footer  the passage's citation (or caption, or the disclaimer)
 *
 * Every page of a passage shows that passage's citation, so a reader never
 * sees manual text without its source. The only words written here rather than
 * taken from the cartridge are UI labels (menu items, "Library", the battery
 * lock note); medical wording always comes from the cartridge. */
#include "ui.h"
#include <string.h>
#include <stdio.h>

#define MAXP 64                          /* passages per node we can page through */
#define LIST_MAX 12

/* ---------------------------------------------------------------- drawing */
static void fb_clear(cart_fb_t *fb) { memset(fb->bits, 0, (size_t)fb->stride * fb->h); }
static void fb_px(cart_fb_t *fb, int x, int y) {
    if (x >= 0 && y >= 0 && x < fb->w && y < fb->h) fb->bits[y * fb->stride + (x >> 3)] |= 0x80 >> (x & 7);
}
static void fb_hline(cart_fb_t *fb, int x0, int x1, int y) { for (int x = x0; x <= x1; x++) fb_px(fb, x, y); }
static void fb_xor_rect(cart_fb_t *fb, int x0, int y0, int w, int h) {
    for (int y = y0; y < y0 + h; y++)
        for (int x = x0; x < x0 + w; x++)
            if (x >= 0 && y >= 0 && x < fb->w && y < fb->h) fb->bits[y * fb->stride + (x >> 3)] ^= 0x80 >> (x & 7);
}

/* font style = family * sizes + size (romkit: 0-2 Lexend, 3-5 Bitter) */
static unsigned ST(const ui_t *u, unsigned size) { return u->s->family * cart_font_sizes(u->c) + size; }

typedef struct { int lh0, hdr_h, ftr_y, body_y, body_h; } lay_t;
static lay_t layout(const ui_t *u) {
    lay_t l; l.lh0 = cart_font_line_height(u->c, ST(u, 0));
    l.hdr_h = l.lh0 + 1;
    l.ftr_y = UI_H - l.lh0 - 1;
    l.body_y = l.hdr_h + 3;
    l.body_h = l.ftr_y - 2 - l.body_y;
    return l;
}
static int base_of(const ui_t *u, unsigned size, int top) { int lh = cart_font_line_height(u->c, ST(u, size)); return top + lh - lh / 4; }

/* Draw s at (x, baseline), cut with "..." to fit maxw. Returns width drawn. */
static int draw_fit(ui_t *u, cart_fb_t *fb, unsigned size, int bold, int x, int base, int maxw, const char *s) {
    if (cart_draw_str(u->c, ST(u, size), bold, 0, 0, 0, s) <= maxw) return cart_draw_str(u->c, ST(u, size), bold, fb, x, base, s);
    char t[96]; size_t n = strlen(s); if (n > sizeof t - 4) n = sizeof t - 4;
    while (n > 0) {
        memcpy(t, s, n); strcpy(t + n, "...");
        if (cart_draw_str(u->c, ST(u, size), bold, 0, 0, 0, t) <= maxw) break;
        n--;
    }
    if (!n) strcpy(t, "...");
    return cart_draw_str(u->c, ST(u, size), bold, fb, x, base, t);
}
static int draw_right(ui_t *u, cart_fb_t *fb, unsigned size, int bold, int xr, int base, const char *s) {
    int w = cart_draw_str(u->c, ST(u, size), bold, 0, 0, 0, s);
    cart_draw_str(u->c, ST(u, size), bold, fb, xr - w, base, s);
    return w;
}
/* Word-wrap a plain C string (UI messages only). */
static void draw_wrap(ui_t *u, cart_fb_t *fb, unsigned size, int x, int y, int w, int h, const char *s) {
    int lh = cart_font_line_height(u->c, ST(u, size)), cy = y;
    char line[96];
    while (*s && cy + lh <= y + h) {
        size_t n = 0, brk = 0;
        while (s[n] && s[n] != '\n' && n < sizeof line - 1) {
            memcpy(line, s, n + 1); line[n + 1] = 0;
            if (cart_draw_str(u->c, ST(u, size), 0, 0, 0, 0, line) > w) break;
            n++;
            if (s[n] == ' ') brk = n;
        }
        if (s[n] && s[n] != '\n' && brk) n = brk;
        if (!n && *s != '\n') n = 1;
        memcpy(line, s, n); line[n] = 0;
        cart_draw_str(u->c, ST(u, size), 0, fb, x, base_of(u, size, cy), line);
        cy += lh; s += n;
        while (*s == ' ' || *s == '\n') s++;
    }
}
static const char *T(ui_t *u, uint16_t id, char *buf, size_t cap) {
    if (id == CART_NONE) { buf[0] = 0; return buf; }
    cart_text(u->c, id, 0, buf, cap);
    for (char *p = buf; *p; p++) if (*p == CART_TXT_HEADING || *p == CART_TXT_BULLET || *p == '\n') *p = ' ';
    return buf;
}

/* --------------------------------------------------------- picture drawing */
typedef struct { cart_fb_t *fb; int x0, y0, ymax, crop; } pic_ctx_t;
static void pic_row(void *user, int y, const uint8_t *row, int bpr) {
    pic_ctx_t *p = user; int yy = p->y0 + y;
    if (yy < 0 || yy > p->ymax) return;
    for (int x = 0; x < bpr * 8; x++)
        if (row[x >> 3] & (0x80 >> (x & 7))) fb_px(p->fb, p->x0 + x - p->crop, yy);
}
static void draw_pic(ui_t *u, cart_fb_t *fb, uint16_t id, int y0, int h, cart_pic_t *out) {
    cart_pic_t p;
    if (cart_pic(u->c, id, &p)) return;
    pic_ctx_t ctx = { fb, 0, y0 + (h - (int)p.h) / 2, y0 + h - 1, 0 };
    if (p.w > UI_W) ctx.crop = (p.w - UI_W) / 2; else ctx.x0 = (UI_W - p.w) / 2;   /* pan pictures: centre crop for now */
    if (ctx.y0 < y0) ctx.y0 = y0;                                                  /* too tall: top-align */
    cart_pic_decode(&p, pic_row, &ctx);
    if (out) *out = p;
}

/* ------------------------------------------------------- node page layout */
static int vis_choices(ui_t *u, const cart_node_t *n, uint8_t *out);
typedef struct {
    const cart_node_t *n;
    int fig, nomatch, npas, per, choice_pages, total, nch, grid;
    uint8_t pp[MAXP];                    /* pages per passage */
    uint8_t ch[64];                      /* visible choice indexes */
} pages_t;
enum { SEG_FIG, SEG_PAS, SEG_NOMATCH, SEG_CHOICES };

static void node_pages(ui_t *u, uint16_t id, pages_t *P) {
    lay_t l = layout(u);
    memset(P, 0, sizeof *P);
    P->n = cart_node(u->c, id);
    if (!P->n) { P->total = 1; return; }
    P->fig = P->n->figure != CART_NONE;
    P->nomatch = (P->n->flags & CART_NODE_NO_MATCH) != 0;
    P->npas = P->n->n_passages + P->n->n_lookalikes; if (P->npas > MAXP) P->npas = MAXP;
    for (int i = 0; i < P->npas; i++) {
        cart_passage_t ps = cart_node_passage(u->c, P->n, i);
        int k = cart_page(u->c, ps.text, ST(u, u->s->font), 0, 0, 4, l.body_y, UI_W - 8, l.body_h, u->scratch, sizeof u->scratch);
        P->pp[i] = (uint8_t)(k < 1 ? 1 : k > 255 ? 255 : k);
        P->total += P->pp[i];
    }
    P->per = l.body_h / cart_font_line_height(u->c, ST(u, u->s->font)); if (P->per < 1) P->per = 1;
    P->nch = vis_choices(u, P->n, P->ch);
    P->grid = u->has_window_keys && P->nch && P->nch <= 4;   /* one answer per corner button */
    P->choice_pages = !P->nch ? 0 : P->grid ? 1 : (P->nch + P->per - 1) / P->per;
    P->total += P->fig + P->nomatch + P->choice_pages;
    if (!P->total) P->total = 1;
}
static int locate(const pages_t *P, int page, int *index, int *sub) {
    if (P->fig) { if (page == 0) { *index = 0; *sub = 0; return SEG_FIG; } page--; }
    for (int i = 0; i < P->npas; i++) { if (page < P->pp[i]) { *index = i; *sub = page; return SEG_PAS; } page -= P->pp[i]; }
    if (P->nomatch) { if (page == 0) { *index = 0; *sub = 0; return SEG_NOMATCH; } page--; }
    *index = 0; *sub = page; return SEG_CHOICES;
}
static int first_choice_page(const pages_t *P) { return P->total - P->choice_pages; }

/* ----------------------------------------------------------------- state */
static ui_window_t *W(ui_t *u) { return &u->s->win[u->s->cur]; }

/* Sticky context filter: choices and sections that don't fit the chosen state /
 * season / activity are hidden. Emergency nodes always show (cart_node_visible). */
#define VIS_MAX 64
static int vis_choices(ui_t *u, const cart_node_t *n, uint8_t *out) {
    int k = 0;
    for (unsigned i = 0; n && i < n->n_choices && k < VIS_MAX; i++)
        if (cart_node_visible(u->c, cart_node_choice(u->c, n, i).to, &u->s->ctx)) out[k++] = (uint8_t)i;
    return k;
}
static int vis_trees(ui_t *u, uint8_t *out) {
    int k = 0;
    for (unsigned t = 0; t < u->c->n_trees && k < VIS_MAX; t++)
        if (cart_node_visible(u->c, cart_tree(u->c, (uint16_t)t).root, &u->s->ctx)) out[k++] = (uint8_t)t;
    return k;
}

void ui_init(ui_t *u, const cart_t *c, ui_saved_t *s) {
    u->c = c; u->s = s;
    if (s->magic != UI_SAVED_MAGIC || s->build_id != c->build_id || s->cur >= UI_WINDOWS) {
        memset(s, 0, sizeof *s);
        s->magic = UI_SAVED_MAGIC; s->build_id = c->build_id; s->font = 0;            /* smallest size: most text per page */
        for (int i = 0; i < UI_WINDOWS; i++) {            /* window 1 = home tree, then the other trees in order */
            int t = c->n_trees ? (c->home_tree + i) % c->n_trees : 0;
            s->win[i].node = c->n_trees ? cart_tree(c, (uint16_t)t).root : 0;
        }
    }
    unsigned nf = cart_font_sizes(c);
    if (nf && s->font >= nf) s->font = (uint8_t)(nf - 1);
    if (s->family >= cart_font_families(c)) s->family = 0;
    u->mode = M_WAKE; u->back_mode = M_READ;
}

static void clamp_page(ui_t *u) {
    pages_t P; node_pages(u, W(u)->node, &P);
    if (W(u)->page >= P.total) W(u)->page = (uint8_t)(P.total - 1);
    if (P.n && W(u)->sel >= P.nch) W(u)->sel = 0;
}
static void go_node(ui_t *u, uint16_t node) {
    ui_window_t *w = W(u);
    if (!cart_node(u->c, node)) return;
    if (node != w->node) {
        if (w->nhist == UI_HIST) { memmove(w->hist, w->hist + 1, sizeof w->hist - sizeof w->hist[0]); w->nhist--; }
        w->hist[w->nhist++] = w->node;
    }
    w->node = node; w->page = 0; w->sel = 0;
    u->mode = M_READ;
}

/* Text size or font changed: page counts change, so go to the start of the
 * passage being read instead of a page number that now means something else. */
static int cur_passage(ui_t *u) {                 /* passage index, or -1 picture, -2 no-match, -3 choices */
    pages_t P; node_pages(u, W(u)->node, &P);
    int idx, sub, seg = locate(&P, W(u)->page, &idx, &sub);
    return seg == SEG_PAS ? idx : seg == SEG_FIG ? -1 : seg == SEG_NOMATCH ? -2 : -3;
}
static void to_passage(ui_t *u, int k) {
    pages_t P; node_pages(u, W(u)->node, &P);
    int p = 0;
    if (k == -1) p = 0;
    else if (k == -3) p = first_choice_page(&P) + W(u)->sel / P.per;
    else if (k == -2) p = P.total - P.choice_pages - 1;
    else { p = P.fig; for (int i = 0; i < k && i < P.npas; i++) p += P.pp[i]; }
    W(u)->page = (uint8_t)(p < 0 ? 0 : p);
}

/* ------------------------------------------------------------- the menu */
enum { MI_FLASH, MI_SOS, MI_IR, MI_MESH, MI_FONT, MI_FAMILY, MI_CONTEXT, MI_LIBRARY, MI_WINDOW, MI_SIG_OFF, MI_SLEEP };
static int menu_items(const ui_t *u, int *out) {
    int n = 0;
    out[n++] = MI_FLASH; out[n++] = MI_SOS;
    if (u->has_ir) out[n++] = MI_IR;
    if (u->has_mesh) out[n++] = MI_MESH;
    out[n++] = MI_FONT;
    if (cart_font_families(u->c) > 1) out[n++] = MI_FAMILY;
    if (cart_ctx_dims(u->c)) out[n++] = MI_CONTEXT;
    out[n++] = MI_LIBRARY;
    if (!u->has_window_keys) out[n++] = MI_WINDOW;
    out[n++] = MI_SIG_OFF; out[n++] = MI_SLEEP;
    return n;
}
static void menu_label(const ui_t *u, int item, char *b, size_t cap) {
    static const char *sizes[] = { "small", "medium", "large" };
    const char *on = "  [on]";
    switch (item) {
    case MI_FLASH:   snprintf(b, cap, "Flashlight%s", u->signal == SIG_FLASHLIGHT ? on : ""); break;
    case MI_SOS:     snprintf(b, cap, "SOS flash (white)%s", u->signal == SIG_SOS_WHITE ? on : ""); break;
    case MI_IR:      snprintf(b, cap, "SOS flash (IR)%s", u->signal == SIG_IR ? on : ""); break;
    case MI_MESH:    snprintf(b, cap, "Mesh SOS radio%s", u->s->mesh_on ? on : ""); break;
    case MI_FONT:    snprintf(b, cap, "Text size: %s", sizes[u->s->font % 3]); break;
    case MI_FAMILY:  snprintf(b, cap, "Font: %s", cart_font_family_name(u->c, u->s->family)); break;
    case MI_CONTEXT: snprintf(b, cap, "Where and when..."); break;
    case MI_LIBRARY: snprintf(b, cap, "Library"); break;
    case MI_WINDOW:  snprintf(b, cap, "Next window (%d of %d)", u->s->cur + 1, UI_WINDOWS); break;
    case MI_SIG_OFF: snprintf(b, cap, "All signals off"); break;
    default:         snprintf(b, cap, "Sleep now"); break;
    }
}

/* ------------------------------------------------------------- rendering */
static void header(ui_t *u, cart_fb_t *fb, const char *left, const char *right) {
    lay_t l = layout(u);
    char r[40];
    int base = base_of(u, 0, 0);
    snprintf(r, sizeof r, "%s%s", u->listening ? "MIC  " : "", right ? right : "");
    if (u->battery_pct >= 0) snprintf(r + strlen(r), sizeof r - strlen(r), "  %d%%%s", u->battery_pct, u->charging ? "+" : "");
    int rw = draw_right(u, fb, 0, 0, UI_W - 2, base, r);
    draw_fit(u, fb, 0, 1, 2, base, UI_W - 10 - rw, left);
    fb_hline(fb, 0, UI_W - 1, l.hdr_h);
}
static void footer(ui_t *u, cart_fb_t *fb, const char *s) {
    lay_t l = layout(u);
    fb_hline(fb, 0, UI_W - 1, l.ftr_y);
    draw_fit(u, fb, 0, 0, 2, base_of(u, 0, l.ftr_y + 1), UI_W - 4, s);
}
static void draw_list(ui_t *u, cart_fb_t *fb, const char *title, const char *right, int n, int sel,
                      void (*label)(ui_t *, int, char *, size_t), const char *foot) {
    lay_t l = layout(u);
    unsigned size = u->s->font;
    int lh = cart_font_line_height(u->c, ST(u, size)), per = l.body_h / lh; if (per < 1) per = 1;
    int top = (sel / per) * per;
    char b[96];
    header(u, fb, title, right);
    for (int i = top; i < n && i < top + per; i++) {
        int y = l.body_y + (i - top) * lh;
        label(u, i, b, sizeof b);
        draw_fit(u, fb, size, 0, 6, base_of(u, size, y), UI_W - 12, b);
        if (i == sel) fb_xor_rect(fb, 2, y, UI_W - 4, lh);
    }
    if (foot) footer(u, fb, foot);
}
static void lbl_choice(ui_t *u, int i, char *b, size_t cap) {
    cart_choice_t ch = cart_node_choice(u->c, cart_node(u->c, W(u)->node), (unsigned)i);
    T(u, ch.label, b, cap);
}
static void lbl_tree(ui_t *u, int i, char *b, size_t cap) { uint8_t v[VIS_MAX]; vis_trees(u, v); T(u, cart_tree(u->c, v[i]).title, b, cap); }
/* context picker: a dimension's values are "Any" then each labelled bit */
static int ctx_values(ui_t *u, uint8_t *out) {          /* out[0] = 0xFF ("Any"), then bits */
    uint16_t nm; unsigned sh, wd; int k = 0;
    out[k++] = 0xFF;
    if (cart_ctx_dim(u->c, u->ctx_dim, &nm, &sh, &wd)) return k;
    for (unsigned b = sh; b < sh + wd && b < 96; b++) if (cart_ctx_label(u->c, b) != CART_NONE) out[k++] = (uint8_t)b;
    return k;
}
static int ctx_chosen(ui_t *u, unsigned dim, char *b, size_t cap) {   /* "Washington", "Any" */
    uint16_t nm; unsigned sh, wd; size_t w = 0; int n = 0; char t[40];
    b[0] = 0;
    if (!cart_ctx_dim(u->c, dim, &nm, &sh, &wd))
        for (unsigned i = sh; i < sh + wd && i < 96; i++)
            if (u->s->ctx.w[i >> 5] >> (i & 31) & 1) {
                T(u, cart_ctx_label(u->c, i), t, sizeof t);
                if (t[0] >= 'a' && t[0] <= 'z') t[0] -= 32;
                w += (size_t)snprintf(b + w, w < cap ? cap - w : 0, "%s%s", n++ ? ", " : "", t);
                if (w >= cap) break;
            }
    if (!n) snprintf(b, cap, "Any");
    return n;
}
static void lbl_dim(ui_t *u, int i, char *b, size_t cap) {
    uint16_t nm; unsigned sh, wd; char name[24], val[64];
    cart_ctx_dim(u->c, (unsigned)i, &nm, &sh, &wd);
    T(u, nm, name, sizeof name); if (name[0] >= 'a' && name[0] <= 'z') name[0] -= 32;
    ctx_chosen(u, (unsigned)i, val, sizeof val);
    snprintf(b, cap, "%s: %s", name, val);
}
static void lbl_value(ui_t *u, int i, char *b, size_t cap) {
    uint8_t v[97]; ctx_values(u, v);
    if (v[i] == 0xFF) { snprintf(b, cap, "Any"); return; }
    T(u, cart_ctx_label(u->c, v[i]), b, cap);
    if (b[0] >= 'a' && b[0] <= 'z') b[0] -= 32;
    if (u->s->ctx.w[v[i] >> 5] >> (v[i] & 31) & 1) strncat(b, "  [on]", cap - strlen(b) - 1);
}
static void lbl_menu(ui_t *u, int i, char *b, size_t cap) { int it[LIST_MAX]; menu_items(u, it); menu_label(u, it[i], b, cap); }
static void lbl_pick(ui_t *u, int i, char *b, size_t cap) { const cart_node_t *n = cart_node(u->c, u->picks[i]); T(u, n ? n->label : CART_NONE, b, cap); }

/* Answer screen: up to 4 answers, each in the corner next to its button
 * (TL = WIN_L, TR = WIN_R, BL = UP, BR = DOWN). One press answers. */
static const ui_btn_t CORNER[4] = { B_WIN_L, B_WIN_R, B_UP, B_DOWN };
static int corner_of(ui_btn_t b) { for (int i = 0; i < 4; i++) if (CORNER[i] == b) return i; return -1; }
static void fb_corner_mark(cart_fb_t *fb, int corner, int y_top, int y_bot) {
    int right = corner & 1, bottom = corner >> 1;
    for (int r = 0; r < 7; r++) {                            /* filled right triangle in the screen corner */
        int y = bottom ? y_bot - r : y_top + r;
        for (int x = 0; x < 7 - r; x++) fb_px(fb, right ? UI_W - 1 - x : x, y);
    }
}
static void draw_answers(ui_t *u, cart_fb_t *fb, int n, char lab[][64]) {
    lay_t l = layout(u);
    int top = l.body_y - 2, bot = l.ftr_y - 1, mid = (top + bot) / 2, cx = UI_W / 2;
    unsigned size = u->s->font;
    int rows = n > 2 ? 2 : 1;
    if (rows == 2) fb_hline(fb, 0, UI_W - 1, mid);
    for (int y = top; y <= (rows == 2 ? bot : mid); y++) fb_px(fb, cx, y);
    for (int i = 0; i < n && i < 4; i++) {
        int right = i & 1, bottom = i >> 1;
        int y0 = bottom ? mid + 2 : top + 1, y1 = bottom ? bot - 1 : (rows == 2 ? mid - 1 : bot - 1);
        int x0 = right ? cx + 4 : 10, w = cx - 14;
        fb_corner_mark(fb, i, top + 1, bot - 1);
        draw_wrap(u, fb, size, x0, y0 + 1, w, y1 - y0, lab[i]);
    }
}

static void render_read(ui_t *u, cart_fb_t *fb) {
    lay_t l = layout(u);
    ui_window_t *w = W(u);
    char left[64], right[16], lab[48], cite[96];
    pages_t P; node_pages(u, w->node, &P);
    if (!P.n) { header(u, fb, "Bad node", 0); return; }
    if (P.choice_pages && w->page >= first_choice_page(&P))     /* keep the page in step with the highlight */
        w->page = (uint8_t)(first_choice_page(&P) + w->sel / P.per);
    T(u, P.n->label, lab, sizeof lab);
    snprintf(left, sizeof left, "%d  %s", u->s->cur + 1, lab);       /* window number, label */
    snprintf(right, sizeof right, "%d/%d", w->page + 1, P.total);
    header(u, fb, left, right);

    if (u->low_battery && !(P.n->flags & CART_NODE_EMERGENCY)) {          /* battery reserve: emergency pages only */
        draw_wrap(u, fb, 1, 6, l.body_y, UI_W - 12, l.body_h,
                  "Battery low. Crank to open this page. Emergency pages stay open.");
        footer(u, fb, T(u, cart_string(u->c, "DISCLAIMER"), cite, sizeof cite));
        return;
    }
    int idx, sub, seg = locate(&P, w->page, &idx, &sub);
    if (seg == SEG_FIG) {
        cart_pic_t p = {0};
        draw_pic(u, fb, P.n->figure, l.body_y, l.body_h, &p);
        char cap[64]; T(u, p.caption, cap, sizeof cap); T(u, p.citation, lab, sizeof lab);
        /* caption and citation are capped so both fit in cite[96]; the footer cuts to its width anyway */
        snprintf(cite, sizeof cite, "%.48s%s%.44s", cap, cap[0] && lab[0] ? " - " : "", lab);
        footer(u, fb, cite);
    } else if (seg == SEG_PAS) {
        cart_passage_t ps = cart_node_passage(u->c, P.n, (unsigned)idx);
        int y = l.body_y, h = l.body_h;
        if (idx >= P.n->n_passages && sub == 0) {                            /* look-alike passages are marked */
            cart_draw_str(u->c, ST(u, 0), 1, fb, 4, base_of(u, 0, y), "LOOK-ALIKE:");
        }
        cart_page(u->c, ps.text, ST(u, u->s->font), sub, fb, 4, y, UI_W - 8, h, u->scratch, sizeof u->scratch);
        footer(u, fb, T(u, ps.citation, cite, sizeof cite));
    } else if (seg == SEG_NOMATCH) {
        draw_wrap(u, fb, 2, 6, l.body_y, UI_W - 12, l.body_h, T(u, cart_string(u->c, "NO_MATCH"), cite, sizeof cite));
        footer(u, fb, T(u, cart_string(u->c, "DISCLAIMER"), cite, sizeof cite));
    } else if (P.grid) {
        char ab[4][64];
        for (int i = 0; i < P.nch; i++) lbl_choice(u, P.ch[i], ab[i], sizeof ab[i]);
        draw_answers(u, fb, P.nch, ab);
        footer(u, fb, "Press the button by your answer. TALK: read again");
    } else {
        unsigned size = u->s->font; int lh = cart_font_line_height(u->c, ST(u, size));
        int top = sub * P.per;
        char b[64];
        for (int i = top; i < P.nch && i < top + P.per; i++) {
            int y = l.body_y + (i - top) * lh;
            lbl_choice(u, P.ch[i], b, sizeof b);
            draw_fit(u, fb, size, 0, 6, base_of(u, size, y), UI_W - 12, b);
            if (i == w->sel) fb_xor_rect(fb, 2, y, UI_W - 4, lh);
        }
        footer(u, fb, T(u, cart_string(u->c, "DISCLAIMER"), cite, sizeof cite));
    }
}

static void render_wake(ui_t *u, cart_fb_t *fb) {
    lay_t l = layout(u);
    char b[96];
    unsigned big = cart_font_sizes(u->c) ? cart_font_sizes(u->c) - 1 : 0;
    uint16_t t = cart_string(u->c, "WAKE_911");
    if (t != CART_NONE) cart_page(u->c, t, ST(u, big), 0, fb, 8, 10, UI_W - 16, l.ftr_y - 14, u->scratch, sizeof u->scratch);
    fb_hline(fb, 0, UI_W - 1, l.ftr_y);
    T(u, cart_string(u->c, "DISCLAIMER"), b, sizeof b);
    int rw = 0;
    if (u->battery_pct >= 0) { char r[12]; snprintf(r, sizeof r, "%d%%%s", u->battery_pct, u->charging ? "+" : ""); rw = draw_right(u, fb, 0, 0, UI_W - 2, base_of(u, 0, l.ftr_y + 1), r); }
    draw_fit(u, fb, 0, 0, 2, base_of(u, 0, l.ftr_y + 1), UI_W - 10 - rw, b);
}

void ui_render(ui_t *u, cart_fb_t *fb) {
    char b[64];
    fb_clear(fb);
    if (!cart_font_sizes(u->c)) return;                 /* cartridge without fonts: nothing we can draw */
    clamp_page(u);
    switch (u->mode) {
    case M_WAKE: render_wake(u, fb); break;
    case M_READ: render_read(u, fb); break;
    case M_LIBRARY: { uint8_t v[VIS_MAX]; draw_list(u, fb, "Library", 0, vis_trees(u, v), u->list_sel, lbl_tree, 0); break; }
    case M_CTX_DIMS: draw_list(u, fb, "Where and when", 0, (int)cart_ctx_dims(u->c), u->list_sel, lbl_dim, "Pages that don't fit are hidden"); break;
    case M_CTX_VALUES: { uint8_t v[97]; draw_list(u, fb, "Pick one", 0, ctx_values(u, v), u->list_sel, lbl_value, 0); break; }
    case M_MENU: { int it[LIST_MAX]; draw_list(u, fb, "Menu", 0, menu_items(u, it), u->list_sel, lbl_menu, "Hold BACK: front light"); break; }
    case M_PICK:
        if (u->has_window_keys) {
            char ab[4][64];
            for (int i = 0; i < u->npicks; i++) lbl_pick(u, i, ab[i], sizeof ab[i]);
            header(u, fb, "Did you mean...", 0);
            draw_answers(u, fb, u->npicks, ab);
            footer(u, fb, "Press the button by your answer. BACK: cancel");
        } else draw_list(u, fb, "Did you mean...", 0, u->npicks, u->list_sel, lbl_pick, 0);
        break;
    case M_MSG: { lay_t l = layout(u); header(u, fb, "", 0); draw_wrap(u, fb, 1, 6, l.body_y, UI_W - 12, l.body_h, u->msg ? u->msg : "");
                  footer(u, fb, T(u, cart_string(u->c, "DISCLAIMER"), b, sizeof b)); break; }
    }
}

void ui_render_sleep(ui_t *u, cart_fb_t *fb) {
    fb_clear(fb);
    if (u->c->sleep_picture != CART_NONE && u->c->sleep_picture < u->c->n_pics) draw_pic(u, fb, u->c->sleep_picture, 0, UI_H, 0);
    else { ui_mode_t m = u->mode; u->mode = M_WAKE; ui_render(u, fb); u->mode = m; }
}

/* --------------------------------------------------------------- events */
static void step(ui_t *u, int dir, int wrap) {
    ui_window_t *w = W(u);
    pages_t P; node_pages(u, w->node, &P);
    int fc = first_choice_page(&P);
    if (P.choice_pages && w->page >= fc) {                 /* in the choice list: move the highlight */
        int s = w->sel + dir;
        if (s < 0) { w->sel = 0; w->page = (uint8_t)(fc > 0 ? fc - 1 : 0); return; }
        if (s >= P.nch) { if (wrap) { w->sel = 0; w->page = 0; } return; }
        w->sel = (uint8_t)s; w->page = (uint8_t)(fc + s / P.per);
        return;
    }
    int p = w->page + dir;
    if (p < 0) p = 0;
    if (p >= P.total) p = wrap ? 0 : P.total - 1;
    w->page = (uint8_t)p;
    if (P.choice_pages && p == fc) w->sel = 0;
}
static void list_step(ui_t *u, int n, int dir) {
    int s = u->list_sel + dir;
    if (s < 0) s = n - 1;
    if (s >= n) s = 0;
    u->list_sel = (uint8_t)s;
}
static void open_menu(ui_t *u) { u->back_mode = u->mode == M_WAKE ? M_READ : u->mode; u->mode = M_MENU; u->list_sel = 0; }
static void open_library(ui_t *u) {
    u->mode = M_LIBRARY; u->list_sel = 0;
    const cart_node_t *n = cart_node(u->c, W(u)->node);
    uint8_t v[VIS_MAX]; int k = vis_trees(u, v);
    for (int i = 0; n && i < k; i++) if (v[i] == n->tree) u->list_sel = (uint8_t)i;
}
static void talk_short(ui_t *u) {
    ui_window_t *w = W(u);
    pages_t P; node_pages(u, w->node, &P);
    if (!P.n) return;
    if (P.grid && w->page >= first_choice_page(&P)) { w->page = 0; w->sel = 0; }   /* answer screen: read again */
    else if (P.choice_pages && w->page >= first_choice_page(&P)) {
        cart_choice_t ch = cart_node_choice(u->c, P.n, P.ch[w->sel]);
        go_node(u, ch.to);
    } else if (P.choice_pages) { w->page = (uint8_t)first_choice_page(&P); w->sel = 0; }
}
static void back(ui_t *u) {
    ui_window_t *w = W(u);
    if (w->nhist) { w->node = w->hist[--w->nhist]; w->page = 0; w->sel = 0; }
    else open_library(u);
}

static ui_act_t menu_select(ui_t *u, ui_act_t a) {
    int it[LIST_MAX]; int n = menu_items(u, it);
    if (u->list_sel >= n) return a;
    int item = it[u->list_sel], sig = -1;
    switch (item) {
    case MI_FLASH: sig = u->signal == SIG_FLASHLIGHT ? SIG_OFF : SIG_FLASHLIGHT; break;
    case MI_SOS:   sig = u->signal == SIG_SOS_WHITE ? SIG_OFF : SIG_SOS_WHITE; break;
    case MI_IR:    sig = u->signal == SIG_IR ? SIG_OFF : SIG_IR; break;
    case MI_SIG_OFF: sig = SIG_OFF; if (u->s->mesh_on) { u->s->mesh_on = 0; a.mesh = 1; } break;
    case MI_MESH:  u->s->mesh_on = !u->s->mesh_on; a.mesh = 1; break;
    case MI_FONT:  { int k = cur_passage(u); unsigned nf = cart_font_sizes(u->c); u->s->font = (uint8_t)((u->s->font + 1) % (nf ? nf : 1)); to_passage(u, k); return a; }
    case MI_FAMILY: { int k = cur_passage(u); u->s->family = (uint8_t)((u->s->family + 1) % cart_font_families(u->c)); to_passage(u, k); return a; }
    case MI_CONTEXT: u->mode = M_CTX_DIMS; u->list_sel = 0; return a;
    case MI_LIBRARY: open_library(u); return a;
    case MI_WINDOW: u->s->cur = (uint8_t)((u->s->cur + 1) % UI_WINDOWS); u->mode = M_READ; return a;
    case MI_SLEEP: a.sleep_now = 1; return a;
    }
    if (sig >= 0) { a.signal = (int8_t)sig; u->signal = (uint8_t)sig; }
    return a;
}

ui_act_t ui_event(ui_t *u, ui_btn_t b, ui_evkind_t k, int held_ms) {
    ui_act_t a = { RD_PARTIAL, 0, -1, VO_NONE, 0, 0, 0 };

    if (b == B_BACK && k == E_LONG) { a.light_step = 1; a.redraw = RD_NONE; return a; }   /* front light, any screen */

    /* push-to-talk: TALK down starts listening, release recognises. A quick tap is a normal TALK press. */
    if (b == B_TALK && u->has_voice && k == E_PRESS && (u->mode == M_WAKE || u->mode == M_READ)) u->talk_armed = 1;
    if (b == B_TALK && u->talk_armed) {               /* only presses that started on a reading screen */
        if (k == E_PRESS) { a.voice = VO_START; a.redraw = RD_NONE; return a; }
        if (k == E_LONG) { u->listening = 1; return a; }                  /* show MIC */
        if (k == E_SHORT) { a.redraw = RD_NONE; return a; }
        if (k == E_RELEASE) {
            u->talk_armed = 0;
            if (held_ms >= 400) { u->listening = 0; a.voice = VO_STOP; a.redraw = RD_NONE; return a; }
            a.voice = VO_CANCEL; u->listening = 0;
            if (u->mode == M_WAKE) u->mode = M_READ; else talk_short(u);
            return a;
        }
    }
    if (k != E_SHORT && k != E_LONG) { a.redraw = RD_NONE; return a; }

    switch (u->mode) {
    case M_WAKE:
        if (b == B_DOWN && k == E_LONG) open_menu(u);
        else u->mode = M_READ;
        break;
    case M_READ:
        if (k == E_LONG) {
            if (b == B_DOWN) open_menu(u);
            else if (b == B_UP) open_library(u);
            else if (b == B_WIN_L || b == B_WIN_R) {               /* text size */
                unsigned nf = cart_font_sizes(u->c); int k = cur_passage(u);
                if (b == B_WIN_L && u->s->font > 0) u->s->font--;
                if (b == B_WIN_R && u->s->font + 1u < nf) u->s->font++;
                to_passage(u, k);
            } else if (b == B_TALK) talk_short(u);
            else a.redraw = RD_NONE;
            break;
        }
        {                                                  /* answer screen: a corner button answers */
            ui_window_t *w = W(u); pages_t P; node_pages(u, w->node, &P);
            int c = corner_of(b);
            if (P.grid && c >= 0 && w->page >= first_choice_page(&P)) {
                if (c < P.nch) { go_node(u, cart_node_choice(u->c, P.n, P.ch[c]).to); break; }
                if (b == B_UP) { step(u, -1, 0); break; }       /* spare bottom-left: back to the text */
                a.redraw = RD_NONE; break;
            }
        }
        if (b == B_DOWN) step(u, +1, !u->has_window_keys);
        else if (b == B_UP) step(u, -1, 0);
        else if (b == B_TALK) talk_short(u);
        else if (b == B_BACK) back(u);
        else if (b == B_WIN_L) u->s->cur = (uint8_t)((u->s->cur + UI_WINDOWS - 1) % UI_WINDOWS);
        else if (b == B_WIN_R) u->s->cur = (uint8_t)((u->s->cur + 1) % UI_WINDOWS);
        break;
    case M_LIBRARY: case M_MENU: case M_PICK: {
        int it[LIST_MAX];
        uint8_t v[VIS_MAX];
        int n = u->mode == M_LIBRARY ? vis_trees(u, v) : u->mode == M_MENU ? menu_items(u, it) : u->npicks;
        if (b == B_DOWN && k == E_LONG && u->mode != M_MENU) { open_menu(u); break; }
        if (u->mode == M_PICK && u->has_window_keys && k == E_SHORT && corner_of(b) >= 0) {
            int c = corner_of(b);
            if (c < u->npicks) go_node(u, u->picks[c]); else a.redraw = RD_NONE;
            break;
        }
        if (b == B_DOWN) list_step(u, n, +1);
        else if (b == B_UP) list_step(u, n, -1);
        else if (b == B_BACK) u->mode = u->mode == M_MENU ? u->back_mode : M_READ;
        else if (b == B_TALK && n) {
            if (u->mode == M_LIBRARY) go_node(u, cart_tree(u->c, v[u->list_sel]).root);
            else if (u->mode == M_PICK) go_node(u, u->picks[u->list_sel]);
            else a = menu_select(u, a);
        } else a.redraw = RD_NONE;
        break;
    }
    case M_CTX_DIMS: case M_CTX_VALUES: {
        uint8_t v[97];
        int n = u->mode == M_CTX_DIMS ? (int)cart_ctx_dims(u->c) : ctx_values(u, v);
        if (b == B_DOWN) list_step(u, n, +1);
        else if (b == B_UP) list_step(u, n, -1);
        else if (b == B_BACK) {
            if (u->mode == M_CTX_VALUES) { u->mode = M_CTX_DIMS; u->list_sel = u->ctx_dim; }
            else { u->mode = M_MENU; u->list_sel = 0; }
        } else if (b == B_TALK && u->mode == M_CTX_DIMS) { u->ctx_dim = u->list_sel; u->mode = M_CTX_VALUES; u->list_sel = 0; }
        else if (b == B_TALK) {                              /* one value per dimension; "Any" clears it */
            cart_ctx_clear_dim(u->c, &u->s->ctx, u->ctx_dim);
            if (v[u->list_sel] != 0xFF) cart_tags_set(&u->s->ctx, v[u->list_sel]);
            a.ctx_changed = 1;
            u->mode = M_CTX_DIMS; u->list_sel = u->ctx_dim;
        } else a.redraw = RD_NONE;
        break;
    }
    case M_MSG:
        u->mode = u->back_mode;
        break;
    }
    return a;
}

ui_act_t ui_voice(ui_t *u, const int *cmd, const float *prob, int n) {
    ui_act_t a = { RD_PARTIAL, 0, -1, VO_NONE, 0, 0, 0 };
    u->listening = 0;
    u->npicks = 0;
    for (int i = 0; i < n && u->npicks < 3; i++) {             /* distinct nodes, best first */
        uint16_t nd = cart_cmd_node(u->c, (uint16_t)cmd[i]);
        if (nd == CART_NONE) continue;
        int dup = 0; for (int j = 0; j < u->npicks; j++) dup |= u->picks[j] == nd;
        if (!dup) u->picks[u->npicks++] = nd;
    }
    if (!u->npicks) {
        u->back_mode = u->mode == M_WAKE ? M_READ : u->mode; u->mode = M_MSG;
        u->msg = "Didn't catch that. Hold TALK and say it again, or use the buttons.";
    } else if (u->npicks == 1 || prob[0] >= 0.5f) go_node(u, u->picks[0]);
    else { u->mode = M_PICK; u->list_sel = 0; }               /* low confidence: let the reader pick */
    return a;
}
