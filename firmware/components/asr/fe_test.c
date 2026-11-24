/* Front-end check: fe_logmel(audio) vs numpy logmel(audio) (fe_ref.py writes NN.audio + NN.ref). */
#include "fasr_frontend.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
static void *slurp(const char *p, long *n) { FILE *f = fopen(p, "rb"); if (!f) return NULL; fseek(f, 0, 2); *n = ftell(f); rewind(f); void *b = malloc(*n); if (fread(b, 1, *n, f) != (size_t)*n) return NULL; fclose(f); return b; }
int main(int argc, char **argv) {
    int bad = 0;
    for (int a = 2; a < argc; a++) {
        char p[512]; long n;
        snprintf(p, sizeof p, "%s/%s.audio", argv[1], argv[a]); uint8_t *au = slurp(p, &n);
        snprintf(p, sizeof p, "%s/%s.ref", argv[1], argv[a]); uint8_t *rf = slurp(p, &n);
        int ns = au[0] | au[1] << 8 | au[2] << 16, T = rf[0] | rf[1] << 8;
        float *f = malloc(sizeof(float) * 40 * fe_frames(ns));
        int got = fe_logmel((const int16_t *)(au + 4), ns, f);
        const float *ref = (const float *)(rf + 8);
        double worst = 0; long same = 0;
        for (long i = 0; i < (long)T * 40; i++) { double e = fabs(f[i] - ref[i]); if (e > worst) worst = e; same += f[i] == ref[i]; }
        printf("%s: %d frames (ref %d), worst |diff| %.2e, identical %.1f%%\n", argv[a], got, T, worst, 100.0 * same / (T * 40.0));
        bad += got != T || worst > 1e-3;
    }
    return bad != 0;
}
