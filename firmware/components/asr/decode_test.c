/* Decoder golden test: fd_decode must give the Python decode()'s n-best ids, confidences, raw and
 * calibrated reject, veto and untrimmed length on every case (gap/junk within 1e-9).
 *   cc -O2 -o /tmp/fd_test decode_test.c fasr_decode.c -lm
 *   python -c '...' (tools/decode_golden.py writes NN.logp, NN.txt, calib.txt) ; /tmp/fd_test lexi.bin dir N */
#include "fasr_decode.h"
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

int main(int argc, char **argv) {
    if (argc < 4) { fprintf(stderr, "usage: %s lexi.bin dir ncases\n", argv[0]); return 2; }
    long n; void *lb = slurp(argv[1], &n);
    fd_lex_t x;
    if (!lb || !fd_open(&x, lb, n)) { printf("bad lexicon\n"); return 1; }
    char p[512];
    fd_calib_t cal; snprintf(p, sizeof p, "%s/calib.txt", argv[2]);
    FILE *cf = fopen(p, "r");
    for (int i = 0; i < 8; i++) if (fscanf(cf, "%lf", &cal.mean[i]) != 1) return 2;
    for (int i = 0; i < 8; i++) if (fscanf(cf, "%lf", &cal.std[i]) != 1) return 2;
    for (int i = 0; i < 8; i++) if (fscanf(cf, "%lf", &cal.weights[i]) != 1) return 2;
    if (fscanf(cf, "%lf %lf", &cal.bias, &cal.threshold) != 2) return 2;
    fclose(cf);
    void *ws = malloc(fd_ws_size());
    int N = atoi(argv[3]), bad = 0;
    for (int c = 0; c < N; c++) {
        snprintf(p, sizeof p, "%s/%02d.logp", argv[2], c);
        long ln; uint8_t *lp = slurp(p, &ln);
        int T = lp[0] | lp[1] << 8;
        fd_result_t r;
        fd_decode(&x, (const float *)(lp + 8), T, &cal, ws, &r);
        snprintf(p, sizeof p, "%s/%02d.txt", argv[2], c);
        FILE *g = fopen(p, "r");
        int gn, grej, graw, gveto, gfl, gfu; double ggap, gjunk;
        if (fscanf(g, "%d %d %d %d %d %d %lf %lf", &gn, &grej, &graw, &gveto, &gfl, &gfu, &ggap, &gjunk) != 8) return 2;
        int ok = gn == r.n && grej == r.reject && graw == r.reject_raw && gveto == (int)r.veto &&
                 (!gn || (gfl == r.full_len && gfu == r.full_unk)) && fabs(ggap - r.gap) < 1e-9 && fabs(gjunk - r.junk) < 1e-9;
        for (int h = 0; h < gn; h++) {
            int len; if (fscanf(g, "%d", &len) != 1) return 2;
            if (h >= r.n || len != r.h[h].len) ok = 0;
            for (int i = 0; i < len; i++) {
                int id, cf2; if (fscanf(g, "%d %d", &id, &cf2) != 2) return 2;
                if (h < r.n && i < r.h[h].len && (id != r.h[h].id[i] || cf2 != r.h[h].conf[i])) ok = 0;
            }
        }
        fclose(g);
        printf("case %02d: T %3d, n %d, reject %3d (raw %3d)%s top:", c, T, r.n, r.reject, r.reject_raw, r.veto ? " veto" : "");
        for (int i = 0; r.n && i < r.h[0].len; i++) printf(" %s", fd_word(&x, r.h[0].id[i]) ? fd_word(&x, r.h[0].id[i]) : "<unk>");
        printf("  %s\n", ok ? "OK" : "MISMATCH");
        if (!ok) { bad++; printf("   want n %d reject %d raw %d veto %d full %d/%d gap %.6f junk %.6f; got gap %.6f junk %.6f\n", gn, grej, graw, gveto, gfl, gfu, ggap, gjunk, r.gap, r.junk); }
        free(lp);
    }
    printf("%d cases, %d differ\n", N, bad);
    return bad != 0;
}
