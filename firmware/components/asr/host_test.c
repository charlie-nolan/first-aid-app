/* Golden test for the int8 engine: every layer's int8 output and the head logits must equal the
 * speech thread's numpy reference (export_int8.forward_q) exactly; log-probs within 1e-5.
 *   python tools/asrpack.py EXPORT -o asrm.bin && python tools/golden2raw.py EXPORT/golden raw/
 *   cc -O2 -o /tmp/asr_test host_test.c fasr_q8.c -lm && /tmp/asr_test asrm.bin raw/ 00 01 ... */
#include "fasr_q8.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void *slurp(const char *p, long *n) {
    FILE *f = fopen(p, "rb"); if (!f) return NULL;
    fseek(f, 0, SEEK_END); *n = ftell(f); rewind(f);
    void *b = malloc(*n); if (fread(b, 1, *n, f) != (size_t)*n) { fclose(f); free(b); return NULL; }
    fclose(f); return b;
}

typedef struct { const char *dir, *clip; const fq_model_t *m; int bad, layers; } ctx_t;
static const char *layer_name(int i, char *buf) {   /* stem, b0.dw, b0.pw, ... like the export */
    if (i == 0) return "stem";
    sprintf(buf, "b%d_%s", (i - 1) / 2, (i - 1) % 2 ? "pw" : "dw");
    return buf;
}
static void cb(void *vc, int layer, const int8_t *x, int c, int t) {
    ctx_t *k = vc; char nm[32], p[512]; long n;
    snprintf(p, sizeof p, "%s/%s.out_%s", k->dir, k->clip, layer_name(layer, nm));
    uint8_t *g = slurp(p, &n);
    if (!g) { printf("  missing %s\n", p); k->bad++; return; }
    unsigned gc = g[0] | g[1] << 8, gt = g[4] | g[5] << 8;
    k->layers++;
    if ((int)gc != c || (int)gt != t || memcmp(g + 8, x, (size_t)c * t)) {
        int diff = 0; for (long i = 0; i < (long)c * t && 8 + i < n; i++) diff += g[8 + i] != (uint8_t)x[i];
        printf("  %s layer %s: shape %dx%d vs %ux%u, %d values differ\n", k->clip, nm, c, t, gc, gt, diff);
        k->bad++;
    }
    free(g);
}

int main(int argc, char **argv) {
    if (argc < 4) { fprintf(stderr, "usage: %s asrm.bin rawdir clip...\n", argv[0]); return 2; }
    long n; void *blob = slurp(argv[1], &n);
    fq_model_t m;
    if (!blob || !fq_open(&m, blob, n)) { printf("bad model\n"); return 1; }
    printf("model: %d layers, %d tokens, widest %d, e_in %d, %ld bytes\n", m.nl, m.ctok, m.cmax, m.e_in, n);
    int fails = 0;
    for (int a = 3; a < argc; a++) {
        char p[512]; long fn;
        snprintf(p, sizeof p, "%s/%s.feats", argv[2], argv[a]);
        uint8_t *fb = slurp(p, &fn);
        if (!fb) { printf("no %s\n", p); return 2; }
        int T = fb[0] | fb[1] << 8 | fb[2] << 16;
        const float *feats = (const float *)(fb + 8);
        void *ws = malloc(fq_ws_size(&m, T));
        int To = fq_out_len(&m, T);
        int32_t *lg = malloc(sizeof(int32_t) * m.ctok * To);
        ctx_t k = {argv[2], argv[a], &m, 0, 0};
        int got = fq_forward(&m, feats, T, ws, lg, cb, &k);
        snprintf(p, sizeof p, "%s/%s.logits", argv[2], argv[a]);
        uint8_t *gl = slurp(p, &fn);
        if (got != To || !gl || memcmp(gl + 8, lg, sizeof(int32_t) * m.ctok * To)) { printf("  %s logits differ\n", argv[a]); k.bad++; }
        float *lp = malloc(sizeof(float) * m.ctok * To);
        fq_logp(&m, lg, To, lp);
        snprintf(p, sizeof p, "%s/%s.logp", argv[2], argv[a]);
        uint8_t *gp = slurp(p, &fn);
        float worst = 0;
        for (long i = 0; gp && i < (long)m.ctok * To; i++) { float e = fabsf(lp[i] - ((float *)(gp + 8))[i]); if (e > worst) worst = e; }
        if (worst > 1e-5f) k.bad++;
        printf("%s: %d frames -> %d, %d layers checked, logp worst %.2e: %s\n", argv[a], T, To, k.layers, worst, k.bad ? "MISMATCH" : "OK");
        fails += k.bad != 0;
        free(ws); free(lg); free(lp); free(fb); free(gl); free(gp);
    }
    return fails != 0;
}
