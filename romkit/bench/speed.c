#include "romkit/reader/cart.h"
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
static void row(void *u, int y, const uint8_t *r, int bpr) { (void)y; (void)bpr; *(int*)u += r[0]; }
int main(int argc, char **argv) { (void)argc;
    FILE *f = fopen(argv[1], "rb"); fseek(f, 0, SEEK_END); long n = ftell(f); rewind(f);
    uint8_t *rom = malloc(n); if (fread(rom, 1, n, f) != (size_t)n) return 1; fclose(f);
    cart_t c; cart_open(&c, rom, n, 0); static char buf[1 << 16];
    struct timespec a, b; size_t tot = 0; clock_gettime(CLOCK_MONOTONIC, &a);
    for (int r = 0; r < 50; r++) for (unsigned i = 0; i < c.n_texts; i++) tot += cart_text(&c, i, 0, buf, sizeof buf);
    clock_gettime(CLOCK_MONOTONIC, &b);
    double ns = ((b.tv_sec - a.tv_sec) * 1e9 + (b.tv_nsec - a.tv_nsec));
    printf("text: %.1f ns/byte on host\n", ns / tot);
    int sink = 0; cart_pic_t p; clock_gettime(CLOCK_MONOTONIC, &a);
    for (int r = 0; r < 50; r++) { cart_pic(&c, 0, &p); cart_pic_decode(&p, row, &sink); }
    clock_gettime(CLOCK_MONOTONIC, &b);
    printf("picture 0 (%dx%d codec %d): %.0f us on host\n", p.w, p.h, p.codec, ((b.tv_sec - a.tv_sec) * 1e9 + (b.tv_nsec - a.tv_nsec)) / 50e3);
    return 0; }
