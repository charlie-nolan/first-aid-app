/* PC preview of the tether's full-card screen (same layout as draw_full_card in tether.c):
 * renders page P of card KEY from a cart with an NKEY chunk into a 250x122 PBM.
 *   cc -O2 -I../../romkit/reader -o /tmp/card_preview card_preview.c ../../romkit/reader/cart.c
 *   /tmp/card_preview cart.bin out_prefix tree/card [tree/card ...]   (writes every page) */
#include "cart.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static uint8_t fbits[32 * 122]; static cart_fb_t fb = {fbits, 32, 250, 122}; static cart_t cart;
static uint32_t r32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
static void hline(int y) { for (int x = 0; x < 250; x++) fbits[y * 32 + x / 8] |= 0x80 >> (x % 8); }
static void fit(unsigned st, int bold, int x, int y, int w, const char *s) {
    char b[160]; snprintf(b, sizeof b, "%s", s); size_t n = strlen(b);
    while (n > 2 && cart_draw_str(&cart, st, bold, 0, 0, 0, b) > w) { b[--n] = 0; if (n > 2) { b[n - 1] = '.'; b[n - 2] = '.'; } }
    cart_draw_str(&cart, st, bold, &fb, x, y, b);
}
typedef struct { int x0, y0, ymax, crop; } cpic_t;
static void row(void *u, int y, const uint8_t *r, int bpr) {
    cpic_t *p = u; int yy = p->y0 + y; if (yy < 0 || yy > p->ymax || yy >= 122) return;
    for (int x = 0; x < bpr * 8; x++) { int xx = p->x0 + x - p->crop; if (xx >= 0 && xx < 250 && (r[x >> 3] & (0x80 >> (x & 7)))) fbits[yy * 32 + xx / 8] |= 0x80 >> (xx % 8); }
}
int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb"); fseek(f, 0, SEEK_END); long n = ftell(f); rewind(f);
    uint8_t *b = malloc(n); if (fread(b, 1, n, f) != (size_t)n) return 2; fclose(f);
    if (cart_open(&cart, b, n, 0)) return 1;
    const uint8_t *nk = NULL;
    for (int i = 0; i < (b[6] | b[7] << 8); i++) { const uint8_t *d = b + 28 + 12 * i; if (!memcmp(d, "NKEY", 4)) nk = b + r32(d + 4); }
    unsigned S_SMALL = 0, S_MED = 1;
    int ls = cart_font_line_height(&cart, S_SMALL), lm = cart_font_line_height(&cart, S_MED);
    static char scratch[6144];
    for (int a = 3; a < argc; a++) {
        int node = -1;
        for (uint32_t i = 0; nk && i < r32(nk + 4); i++) { const uint8_t *e = nk + 8 + 12 * i; if (!strcmp((const char *)nk + r32(e), argv[a])) node = e[8] | e[9] << 8; }
        if (node < 0) { printf("%s: not in NKEY\n", argv[a]); continue; }
        const cart_node_t *cn = cart_node(&cart, node);
        int pages[64][3], np = 0;
        for (int i = 0; i < cn->n_passages + cn->n_lookalikes; i++) {
            cart_passage_t ps = cart_node_passage(&cart, cn, i);
            int y = ls + 3, by = y + lm + 1, bh = 122 - ls - 2 - by;
            int k = cart_page(&cart, ps.text, S_SMALL, 0, 0, 3, by, 244, bh, scratch, sizeof scratch);
            for (int s = 0; s < k && np < 64; s++) { pages[np][0] = 0; pages[np][1] = i; pages[np][2] = s; np++; }
        }
        if (cn->figure != CART_NONE && np < 64) { pages[np][0] = 1; np++; }
        for (int pg = 0; pg < np; pg++) {
            memset(fbits, 0, sizeof fbits);
            char top[160], lab[120], cite[100] = "", foot[140];
            snprintf(top, sizeof top, "CARD  heard: (voice test)");
            fit(S_SMALL, 1, 3, ls - 2, 244, top); hline(ls + 1);
            int y = ls + 3, bottom = 122 - ls - 2;
            cart_text(&cart, cn->label, 0, lab, sizeof lab);
            fit(S_MED, 1, 3, y + lm - lm / 4, 244, lab);
            int by = y + lm + 1, bh = bottom - by;
            if (pages[pg][0] == 0) {
                cart_passage_t ps = cart_node_passage(&cart, cn, pages[pg][1]);
                cart_page(&cart, ps.text, S_SMALL, pages[pg][2], &fb, 3, by, 244, bh, scratch, sizeof scratch);
                cart_text(&cart, ps.citation, 0, cite, sizeof cite);
            } else {
                cart_pic_t p; if (!cart_pic(&cart, cn->figure, &p)) {
                    cpic_t c = {0, by + (bh - (int)p.h) / 2, bottom - 1, 0};
                    if (p.w > 250) c.crop = (p.w - 250) / 2; else c.x0 = (250 - p.w) / 2;
                    if (c.y0 < by) c.y0 = by;
                    cart_pic_decode(&p, row, &c); cart_text(&cart, p.citation, 0, cite, sizeof cite);
                }
            }
            hline(122 - ls - 1);
            snprintf(foot, sizeof foot, "%d/%d %s", pg + 1, np, cite); fit(S_SMALL, 0, 3, 121 - ls / 4, 244, foot);
            char path[512]; snprintf(path, sizeof path, "%s_%s_p%d.pbm", argv[2], argv[a], pg + 1);
            for (char *q = path + strlen(argv[2]); *q; q++) if (*q == '/') *q = '-';
            FILE *o = fopen(path, "wb"); fprintf(o, "P4\n250 122\n");
            for (int yy = 0; yy < 122; yy++) fwrite(fbits + yy * 32, 1, 32, o);
            fclose(o);
        }
        printf("%s: node %d, %d pages\n", argv[a], node, np);
    }
    return 0;
}
