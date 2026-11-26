/* Trunk -> H -> tail check: the base model run whole vs its first a layers + the EXPT tail of layers a..n-1 run on
 * the saved H (fq_forward_h). With a tail cut from the base itself (tools/tailslice.py) the logits must be identical.
 *   gcc -O2 -DFE_FAST -ffp-contract=off -o tail_test tail_test.c fasr_q8.c fasr_frontend.c -lm
 *   tail_test asrm.bin tail.expt <raw dir> 00 01 ...        (raw dir: tools/golden2raw.py output, NN.audio) */
#include "fasr_frontend.h"
#include "fasr_q8.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static void *slurp(const char *p, long *n) { FILE *f = fopen(p, "rb"); if (!f) return NULL; fseek(f, 0, 2); *n = ftell(f); rewind(f); void *b = malloc(*n); if (fread(b, 1, *n, f) != (size_t)*n) return NULL; fclose(f); return b; }
typedef struct { int want; int8_t *h; int c, t; } cap_t;
static void cb(void *ctx, int layer, const int8_t *x, int c, int t) {
    cap_t *k = ctx;
    if (layer == k->want) { k->h = malloc((size_t)c * t); memcpy(k->h, x, (size_t)c * t); k->c = c; k->t = t; }
}
int main(int argc, char **argv) {
    long bn, tn; void *bb = slurp(argv[1], &bn), *tb = slurp(argv[2], &tn);
    fq_model_t base; fq_tail_t tail;
    if (!bb || !tb) { printf("missing file\n"); return 2; }
    if (!fq_open(&base, bb, bn)) { printf("bad base\n"); return 2; }
    if (!fq_tail_open(&tail, tb, tn, NULL)) { printf("bad tail\n"); return 2; }
    printf("tail '%s': layers %u..%u, e_h %d\n", tail.group, tail.layer_a, tail.layer_b - 1, tail.e_h);
    int bad = 0;
    for (int a = 4; a < argc; a++) {
        char p[512]; long n; snprintf(p, sizeof p, "%s/%s.audio", argv[3], argv[a]);
        uint8_t *au = slurp(p, &n); if (!au) continue;
        int ns = au[0] | au[1] << 8 | au[2] << 16, T = fe_frames(ns), To = fq_out_len(&base, T);
        float *f = malloc(sizeof(float) * 40 * T); fe_logmel((const int16_t *)(au + 4), ns, f);
        void *ws = malloc(fq_ws_size(&base, T)); int32_t *l1 = malloc(4L * base.ctok * To), *l2 = malloc(4L * base.ctok * To);
        cap_t k = {tail.layer_a - 1, 0, 0, 0};
        int g1 = fq_forward(&base, f, T, ws, l1, cb, &k);
        int g2 = k.h ? fq_forward_h(&tail.m, k.h, k.c, k.t, ws, l2) : -1;
        int same = g1 == g2 && g1 > 0 && !memcmp(l1, l2, 4L * base.ctok * g1);
        printf("%s: %d frames, H %d x %d, trunk+tail vs whole: %s\n", argv[a], g1, k.c, k.t, same ? "identical" : "DIFFER");
        bad += !same;
        free(f); free(ws); free(l1); free(l2); free(k.h); free(au);
    }
    return bad != 0;
}
