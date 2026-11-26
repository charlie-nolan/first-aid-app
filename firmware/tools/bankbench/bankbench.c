/* bankbench.c - Stage 2 benchmark (firmware/docs/EXPERT_BANKS_SPEC.md), the board's own C code on the PC.
 *
 *   bankbench pass1  <cart> <list.tsv> <logp_dir>    list: wav<TAB>label per line -> JSON lines on stdout:
 *       Pass 1 = front end (FE_FAST) + int8 model + search with the cart's global LEXI, the trees keep_trees
 *       picks from the n-best, the trees keep_trees picks from the label (oracle); logp saved as <i>.logp
 *   bankbench decode <lexi.bin>                      stdin: <logp file> per line -> JSON: n-best + reject
 *   bankbench fill   <cart>                          stdin: tree<TAB>text -> JSON: the blanks _fill gives
 *
 * Build (from firmware/):  gcc -O2 -DFE_FAST -ffp-contract=off -Icomponents/asr -Icomponents/vform \
 *   -o bankbench tools/bankbench/bankbench.c components/asr/fasr_frontend.c components/asr/fasr_q8.c \
 *   components/asr/fasr_decode.c components/vform/vform.c -lm */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "fasr_decode.h"
#include "fasr_frontend.h"
#include "fasr_q8.h"
#include "vform.h"

static void *slurp(const char *p, long *n) {
    FILE *f = fopen(p, "rb"); if (!f) return NULL;
    fseek(f, 0, SEEK_END); *n = ftell(f); rewind(f);
    void *b = malloc(*n ? *n : 1);
    if (fread(b, 1, *n, f) != (size_t)*n) { fclose(f); free(b); return NULL; }
    fclose(f); return b;
}
static void jstr(const char *s) {
    putchar('"');
    for (; *s; s++) { if (*s == '"' || *s == '\\') putchar('\\'); putchar(*s >= 32 ? *s : ' '); }
    putchar('"');
}
static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec * 1e-9; }

static fd_lex_t X; static fd_calib_t CAL; static int have_cal; static void *ws;
static void nbest_json(const fd_result_t *r, char texts[][400]) {
    printf("\"reject\":%d,\"nbest\":[", r->reject);
    for (int h = 0; h < r->n; h++) {
        size_t o = 0; texts[h][0] = 0;
        for (int k = 0; k < r->h[h].len; k++) {
            const char *w = fd_word(&X, r->h[h].id[k]);
            o += snprintf(texts[h] + o, 400 - o, "%s%s", o ? " " : "", w ? w : "<unk>");
        }
        if (h) putchar(','); jstr(texts[h]);
    }
    printf("]");
}
static void open_cal(const uint8_t *c, size_t cl) {
    have_cal = c && cl >= 4 + 26 * 8 && !memcmp(c, "RJC1", 4);
    if (!have_cal) return;
    double v[26]; memcpy(v, c + 4, sizeof v);
    memcpy(CAL.mean, v, 64); memcpy(CAL.std, v + 8, 64); memcpy(CAL.weights, v + 16, 64);
    CAL.bias = v[24]; CAL.threshold = v[25];
}
static void trees_json(fp_sess_t *s, const char *const *h, int nh, const char *pname) {
    int n = fp_keep_trees(s, h, nh, 3);
    printf("[");
    for (int i = 0; i < n; i++) { if (i) putchar(','); jstr(fp_s(&s->pk, s->trees[s->cands[i].tree].name)); }
    printf("],\"%s\":[", pname);
    for (int i = 0; i < n; i++) printf("%s%.3f", i ? "," : "", s->cands[i].p);
    printf("]");
}

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "see the header\n"); return 2; }
    ws = malloc(fd_ws_size());
    if (!strcmp(argv[1], "decode")) {
        long ln; void *lb = slurp(argv[2], &ln);
        if (!lb || !fd_open(&X, lb, ln)) { fprintf(stderr, "bad lexi\n"); return 1; }
        if (argc > 3) { long cn; void *cb = slurp(argv[3], &cn); open_cal(cb, cn); }   /* optional ASRC */
        char line[1024];
        while (fgets(line, sizeof line, stdin)) {
            line[strcspn(line, "\n")] = 0;
            long n; float *lp = slurp(line, &n);
            if (!lp) { printf("{}\n"); continue; }
            int T = (int)(n / (sizeof(float) * X.ntok));
            static fd_result_t r; static char tx[FD_MAX_HYPS][400];
            double t0 = now();
            fd_decode(&X, lp, T, have_cal ? &CAL : NULL, ws, &r);
            printf("{\"ms\":%.2f,", (now() - t0) * 1000); nbest_json(&r, tx); printf("}\n"); fflush(stdout);
            free(lp);
        }
        return 0;
    }
    long cn; uint8_t *cart = slurp(argv[2], &cn);
    if (!cart) return 1;
    size_t fl, ll, ml, al;
    const void *form = fp_cart_chunk(cart, cn, "FORM", &fl), *lexi = fp_cart_chunk(cart, cn, "LEXI", &ll);
    const void *asrm = fp_cart_chunk(cart, cn, "ASRM", &ml); const uint8_t *asrc = fp_cart_chunk(cart, cn, "ASRC", &al);
    static fp_sess_t s;
    if (!form || !fp_sess_init(&s, form, fl)) { fprintf(stderr, "no FORM\n"); return 1; }
    if (!strcmp(argv[1], "fill")) {
        char line[2048];
        while (fgets(line, sizeof line, stdin)) {
            line[strcspn(line, "\n")] = 0;
            char *tab = strchr(line, '\t'); if (!tab) { printf("[]\n"); continue; }
            *tab = 0;
            int t = fp_tree_by_name(&s, line);
            fp_filled_t f; f.n = 0;
            if (t >= 0) fp_fill(&s, t, tab + 1, &f);
            printf("[");
            for (int i = 0; i < f.n; i++) { if (i) putchar(','); printf("["); jstr(fp_s(&s.pk, f.slot[i])); putchar(','); jstr(fp_s(&s.pk, f.val[i])); printf("]"); }
            printf("]\n"); fflush(stdout);
        }
        return 0;
    }
    /* pass1 */
    fq_model_t m;
    if (!lexi || !asrm || !fd_open(&X, lexi, ll) || !fq_open(&m, asrm, ml)) { fprintf(stderr, "no speech chunks\n"); return 1; }
    open_cal(asrc, al);
    FILE *L = fopen(argv[3], "r"); if (!L) return 1;
    char line[4096]; int idx = 0;
    while (fgets(line, sizeof line, L)) {
        line[strcspn(line, "\n")] = 0;
        char *tab = strchr(line, '\t'); const char *label = tab ? tab + 1 : "";
        if (tab) *tab = 0;
        long wn; uint8_t *w = slurp(line, &wn);
        if (!w || wn < 44) { idx++; continue; }
        int ns = (int)((wn - 44) / 2); if (ns > 16000 * 15) ns = 16000 * 15;
        int T = fe_frames(ns), To = fq_out_len(&m, T);
        float *feats = malloc(sizeof(float) * 40 * T); void *mws = malloc(fq_ws_size(&m, T));
        int32_t *lg = malloc(sizeof(int32_t) * m.ctok * To); float *lp = malloc(sizeof(float) * m.ctok * To);
        double t0 = now();
        fe_logmel((const int16_t *)(w + 44), ns, feats);
        int got = fq_forward(&m, feats, T, mws, lg, NULL, NULL);
        fq_logp(&m, lg, got, lp);
        double t1 = now();
        static fd_result_t r; static char tx[FD_MAX_HYPS][400];
        fd_decode(&X, lp, got, have_cal ? &CAL : NULL, ws, &r);
        double t2 = now();
        char p[1200]; snprintf(p, sizeof p, "%s/%d.logp", argv[4], idx);
        FILE *o = fopen(p, "wb"); if (o) { fwrite(lp, sizeof(float), (size_t)m.ctok * got, o); fclose(o); }
        printf("{\"i\":%d,\"wav\":", idx); jstr(line); printf(",\"label\":"); jstr(label);
        printf(",\"frames\":%d,\"model_ms\":%.1f,\"search_ms\":%.2f,", got, (t1 - t0) * 1000, (t2 - t1) * 1000);
        nbest_json(&r, tx);
        const char *hy[FD_MAX_HYPS]; for (int h = 0; h < r.n; h++) hy[h] = tx[h];
        printf(",\"trees\":"); if (r.n) trees_json(&s, hy, r.n, "p"); else printf("[],\"p\":[]");
        const char *lab[1] = {label};
        printf(",\"oracle_trees\":"); trees_json(&s, lab, 1, "oracle_p");
        printf("}\n"); fflush(stdout);
        free(feats); free(mws); free(lg); free(lp); free(w); idx++;
    }
    return 0;
}
