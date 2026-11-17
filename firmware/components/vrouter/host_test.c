/* Host test: the C reader must give the same scores as the Python int8 reference.
 *   cc -O2 -o /tmp/vr_test host_test.c vrouter.c -lm && /tmp/vr_test model.vxp model.golden.txt */
#include "vrouter.h"
#include <stdio.h>
#include <stdlib.h>
#include <math.h>

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: %s model.vxp golden.txt\n", argv[0]); return 2; }
    FILE *f = fopen(argv[1], "rb"); fseek(f, 0, SEEK_END); long n = ftell(f); rewind(f);
    void *blob = malloc(n); if (fread(blob, 1, n, f) != (size_t)n) return 2; fclose(f);
    vr_model_t m;
    if (!vr_open(&m, blob, n)) { fprintf(stderr, "bad blob\n"); return 1; }
    printf("model kind %d: %d words x %d, %d hash rows, %d ctx, hidden %d, %d classes, %ld bytes\n",
           m.kind, m.V, m.d, m.B, m.C, m.hidden, m.k, n);
    FILE *g = fopen(argv[2], "r");
    int ctx, len, k, cases = 0, bad = 0; float worst = 0;
    while (fscanf(g, "%d %d", &ctx, &len) == 2) {
        voice_hyp_t h = { .n = 1 };
        h.h[0].len = len > VR_MAX_WORDS ? VR_MAX_WORDS : len;
        for (int i = 0; i < len; i++) { int id; if (fscanf(g, "%d", &id) != 1) return 2; if (i < VR_MAX_WORDS) { h.h[0].id[i] = id; h.h[0].conf[i] = 100; } }
        if (fscanf(g, "%d", &k) != 1 || k > VR_MAX_CLASSES) return 2;
        float want[VR_MAX_CLASSES], p[VR_MAX_CLASSES], mx = -1e30f, s = 0;
        for (int i = 0; i < k; i++) { if (fscanf(g, "%f", &want[i]) != 1) return 2; if (want[i] > mx) mx = want[i]; }
        for (int i = 0; i < k; i++) { want[i] = expf(want[i] - mx); s += want[i]; }
        vr_score(&m, &h, ctx, p);
        int t[3], wt[3], wa;
        bool auto_ok = vr_tiles(&m, &h, p, t);
        if (fscanf(g, "%d %d %d %d", &wt[0], &wt[1], &wt[2], &wa) != 4) return 2;
        if (t[0] != wt[0] || t[1] != wt[1] || t[2] != wt[2] || auto_ok != (wa != 0)) {
            printf("  case %d tiles %d %d %d auto %d, want %d %d %d auto %d\n", cases, t[0], t[1], t[2], auto_ok, wt[0], wt[1], wt[2], wa);
            bad++;
        }
        for (int i = 0; i < k; i++) { float e = fabsf(p[i] - want[i] / s); if (e > worst) worst = e; if (e > 1e-3f) bad++; }
        printf("  case %d -> %s (%.2f) | %s | %s%s\n", cases, vr_name(&m, t[0]), p[t[0]], vr_name(&m, t[1]), vr_name(&m, t[2]), auto_ok ? "" : "  [no auto]");
        cases++;
    }
    printf("%d cases, worst prob difference %.2e, %s\n", cases, worst, bad ? "MISMATCH" : "OK");
    return bad != 0;
}
