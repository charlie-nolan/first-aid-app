/* Check an NKEY chunk against the cart it rides in: every key's node must have the same label in
 * the cart (node ids shift between bakes, so a stale map would open wrong cards).
 *   cc -O2 -I../../romkit/reader -o /tmp/nkey_check nkey_check.c ../../romkit/reader/cart.c && /tmp/nkey_check cart.bin */
#include "cart.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static uint32_t r32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb"); if (!f) return 2;
    fseek(f, 0, SEEK_END); long n = ftell(f); rewind(f);
    uint8_t *b = malloc(n); if (fread(b, 1, n, f) != (size_t)n) return 2; fclose(f);
    cart_t c; if (cart_open(&c, b, n, 1)) { printf("cart does not open\n"); return 1; }
    const uint8_t *nk = NULL;
    for (int i = 0; i < (b[6] | b[7] << 8); i++) { const uint8_t *d = b + 28 + 12 * i; if (!memcmp(d, "NKEY", 4)) nk = b + r32(d + 4); }
    if (!nk || memcmp(nk, "NK01", 4)) { printf("no NKEY chunk\n"); return 1; }
    uint32_t m = r32(nk + 4); int bad = 0, empty = 0;
    static char t[512];
    for (uint32_t i = 0; i < m; i++) {
        const uint8_t *e = nk + 8 + 12 * i;
        const char *key = (const char *)nk + r32(e), *label = (const char *)nk + r32(e + 4);
        uint16_t node = e[8] | e[9] << 8;
        const cart_node_t *cn = cart_node(&c, node);
        t[0] = 0;
        if (cn) cart_text(&c, cn->label, 0, t, sizeof t);
        if (!cn || strcmp(t, label)) { if (bad < 5) printf("  %s -> node %u: cart says '%s', map says '%s'\n", key, node, t, label); bad++; }
        if (!label[0]) empty++;
    }
    printf("%u keys, %d disagree with the cart, %d without a label\n", m, bad, empty);
    return bad != 0;
}
