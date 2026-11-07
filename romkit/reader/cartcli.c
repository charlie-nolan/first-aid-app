/* Host CLI around the reader, for the builder's test mode.
 *   cartcli LIB.bin cmd N     -> node id for ESP-SR command N
 *   cartcli LIB.bin text N    -> text N
 *   cartcli LIB.bin node N    -> label, passages with citations, choices
 *   cartcli LIB.bin string NAME
 *   cartcli LIB.bin info */
#include "cart.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static char buf[1 << 16];
static const char *T(const cart_t *c, uint16_t id) { cart_text(c, id, 0, buf, sizeof buf); return id == CART_NONE ? "" : buf; }
/* Print text with the device control bytes turned back into the builder's
 * markers ("## " heading, "- " bullet), so it matches passages.jsonl verbatim. */
static void P(const cart_t *c, uint16_t id) {
    for (const char *s = T(c, id); *s; s++)
        if (*s == CART_TXT_HEADING) fputs("## ", stdout);
        else if (*s == CART_TXT_BULLET) fputs("- ", stdout);
        else putchar(*s);
}
/* CTX on the command line: comma-separated tag labels, e.g. "Washington,winter"; "-" = none */
static cart_tags_t parse_ctx(const cart_t *c, const char *s) {
    cart_tags_t t = {{0, 0, 0}}; char word[64];
    while (*s && strcmp(s, "-")) {
        size_t n = strcspn(s, ","); if (n >= sizeof word) n = sizeof word - 1;
        memcpy(word, s, n); word[n] = 0; s += n + (s[n] == ',');
        for (unsigned b = 0; b < 96; b++) { uint16_t l = cart_ctx_label(c, b); if (l != CART_NONE && !strcmp(T(c, l), word)) cart_tags_set(&t, b); }
    }
    return t;
}
int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: cartcli LIB.bin cmd|text|node|string|render|voice|visible|contexts|info [ARG]\n"); return 2; }
    FILE *f = fopen(argv[1], "rb"); if (!f) { perror(argv[1]); return 1; }
    fseek(f, 0, SEEK_END); long n = ftell(f); rewind(f);
    uint8_t *rom = malloc(n); if (fread(rom, 1, n, f) != (size_t)n) return 1; fclose(f);
    cart_t c; int rc = cart_open(&c, rom, n, 1);
    if (rc) { fprintf(stderr, "cart_open failed: %d\n", rc); return 1; }
    const char *op = argv[2]; long a = argc > 3 ? strtol(argv[3], 0, 10) : 0;
    if (!strcmp(op, "cmd")) { uint16_t nd = cart_cmd_node(&c, (uint16_t)a); if (nd == CART_NONE) return 1; printf("%u\n", nd); }
    else if (!strcmp(op, "text")) { P(&c, (uint16_t)a); putchar('\n'); }
    else if (!strcmp(op, "string")) { uint16_t id = cart_string(&c, argv[3]); if (id == CART_NONE) return 1; puts(T(&c, id)); }
    else if (!strcmp(op, "node")) {
        const cart_node_t *nd = cart_node(&c, (uint16_t)a); if (!nd) return 1;
        printf("[%ld] %s  (flags %u, figure %d)\n", a, T(&c, nd->label), nd->flags, nd->figure == CART_NONE ? -1 : nd->figure);
        for (unsigned i = 0; i < nd->n_passages + nd->n_lookalikes; i++) {
            cart_passage_t p = cart_node_passage(&c, nd, i);
            fputs(i >= nd->n_passages ? "LOOK-ALIKE: " : "", stdout); P(&c, p.text); putchar('\n');
            printf("  -- %s\n", T(&c, p.citation));
        }
        for (unsigned i = 0; i < nd->n_choices; i++) {
            cart_choice_t ch = cart_node_choice(&c, nd, i);
            printf("  > %s -> %u (kind %u)\n", T(&c, ch.label), ch.to, ch.kind);
        }
    } else if (!strcmp(op, "render") && argc > 6) {        /* render TEXT SIZE PAGE out.pbm: 250x122 screen */
        static uint8_t bits[32 * 122]; static char scratch[1 << 15];
        cart_fb_t fb = { bits, 32, 250, 122 };
        int pages = cart_page(&c, (uint16_t)a, (unsigned)atoi(argv[4]), atoi(argv[5]), &fb, 4, 2, 242, 118, scratch, sizeof scratch);
        FILE *o = fopen(argv[6], "wb"); fprintf(o, "P4\n256 122\n"); fwrite(bits, 1, sizeof bits, o); fclose(o);
        printf("%d pages\n", pages);
    } else if (!strcmp(op, "voice") && argc > 4) {         /* voice TREE|home CTX: the ESP-SR list */
        static uint16_t ids[CART_VOICE_MAX];
        uint16_t tree = strcmp(argv[3], "home") ? (uint16_t)a : CART_NONE;
        cart_tags_t ctx = parse_ctx(&c, argv[4]);
        int k = cart_voice_list(&c, tree, &ctx, ids, CART_VOICE_MAX);
        for (int i = 0; i < k; i++) { cart_phrase_t p = cart_phrase(&c, ids[i]); printf("%u\t%s\t-> %u\n", ids[i], T(&c, p.text), p.node); }
    } else if (!strcmp(op, "visible") && argc > 4) {        /* visible NODE CTX */
        cart_tags_t ctx = parse_ctx(&c, argv[4]);
        printf("%d\n", cart_node_visible(&c, (uint16_t)a, &ctx));
    } else if (!strcmp(op, "contexts")) {                   /* list dimensions and tag bits */
        for (unsigned d = 0; d < cart_ctx_dims(&c); d++) {
            uint16_t nm; unsigned s, w; cart_ctx_dim(&c, d, &nm, &s, &w); printf("%s (bits %u-%u):", T(&c, nm), s, s + w - 1);
            for (unsigned b = s; b < s + w; b++) if (cart_ctx_label(&c, b) != CART_NONE) printf(" [%u] %s", b, T(&c, cart_ctx_label(&c, b)));
            putchar('\n');
        }
    } else if (!strcmp(op, "info"))
        printf("build %08x, %u nodes, %u texts, %u syms, %u cmds, %u pictures, %u strings, %u bytes\n",
               (unsigned)c.build_id, c.n_nodes, c.n_texts, c.n_syms, c.n_cmds, c.n_pics, c.n_strs, (unsigned)c.size);
    else return 2;
    return 0;
}
