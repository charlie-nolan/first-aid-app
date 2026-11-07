/* Host test: dumps every text and picture so Python can compare with the source. */
#include "cart.h"
#include <stdio.h>
#include <stdlib.h>
static FILE *pf;
static void row(void *u, int y, const uint8_t *r, int bpr) { (void)u; (void)y; fwrite(r, 1, bpr, pf); }
int main(int argc, char **argv) { (void)argc;
    FILE *f = fopen(argv[1], "rb"); fseek(f, 0, SEEK_END); long n = ftell(f); rewind(f);
    uint8_t *rom = malloc(n); fread(rom, 1, n, f); fclose(f);
    cart_t c; int rc = cart_open(&c, rom, n, 1);
    if (rc) { printf("open failed %d\n", rc); return 1; }
    FILE *o = fopen(argv[2], "wb"); static char buf[1 << 16];
    for (unsigned i = 0; i < c.n_texts; i++) {
        size_t len = cart_text(&c, i, 0, buf, sizeof buf);
        fwrite(&len, 4, 1, o); fwrite(buf, 1, len, o);
    }
    fclose(o);
    pf = fopen(argv[3], "wb");
    for (unsigned i = 0; i < c.n_pics; i++) { cart_pic_t p; cart_pic(&c, i, &p); cart_pic_decode(&p, row, 0); }
    fclose(pf);
    /* paging check: second "screen" of text 5 */
    char page[64]; cart_text(&c, 5, 40, page, sizeof page);
    /* voice path: cmd 0 -> node -> first passage */
    uint16_t node = cart_cmd_node(&c, 0); const cart_node_t *nd = cart_node(&c, node);
    cart_passage_t ps = cart_node_passage(&c, nd, 0);
    char lab[48], cite[64], body[90];
    cart_text(&c, nd->label, 0, lab, sizeof lab); cart_text(&c, ps.citation, 0, cite, sizeof cite);
    cart_text(&c, ps.text, 0, body, sizeof body);
    printf("cmd 0 -> node %u '%s' [%s]\n  %s...\n", node, lab, cite, body);
    printf("texts %u syms %u nodes %u cmds %u pics %u\n", c.n_texts, c.n_syms, c.n_nodes, c.n_cmds, c.n_pics);
    return 0;
}
