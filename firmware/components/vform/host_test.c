/* Host parity test for vform: replays a script of sentences and taps and prints every SHOW frame,
 * one per line, for tools/parity_check.py to compare with the Python's frames.
 *   cc -O2 -ffp-contract=off -o /tmp/vform_test host_test.c vform.c vform_show.c -lm
 *   /tmp/vform_test form.fpk script.txt > frames.txt
 * Script lines (tab separated, \N = null):
 *   CASE <id>            new case: fresh screen
 *   SAY <text>           a spoken sentence or answer
 *   PICK <field> <value|\N> <node|\N>
 *   HINT <text>          tether form hint (pc_bridge.py's msg on form frames); CASE clears it
 *   SKIP - | OPEN <node> | NEW - | AGAIN <reject>\t<text>   (the tether's actions)
 *   FUZZ <text>          tap-loop finder (vr.form.fuzz): first then last choice, every tree
 * script "-" reads the lines from stdin and flushes each frame (tether/fake_board.py drives it). */
#include "vform_show.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static char *field(char **p) {
    char *s = *p, *t = strchr(s, '\t');
    if (t) { *t = 0; *p = t + 1; } else *p = s + strlen(s);
    return strcmp(s, "\\N") ? s : NULL;
}

static int fuzz(fp_sess_t *s, const char *text, int depth) {
    int bad = 0;
    for (int end = 0; end < 2; end++)
        for (int t = 0; t < s->ntrees; t++) {
            s->ncands = 1;
            memset(&s->cands[0], 0, sizeof s->cands[0]);
            s->cands[0].tree = (uint8_t)t; s->cands[0].p = 1.0; s->cands[0].asking = FP_NONE;
            const char *h[1] = {text};
            fp_result_t r = fp_first_sentence(s, h, 1, false);
            for (int d = 0; d < depth && r.kind == FP_FORM; d++) {
                fp_field_t f[FP_MAX_FIELDS];
                int nf = fp_open_fields(s, f, FP_MAX_FIELDS);
                if (!nf || f[0].key == FP_TOPIC) break;
                fp_tile_t ts[32], ch[32]; int nc = 0;
                int nt = fp_tiles(s, f[0].tree, f[0].node, ts, 32);
                for (int k = 0; k < nt; k++) if (f[0].when_slot == FP_NONE || (ts[k].value != FP_NONE && *fp_s(&s->pk, ts[k].value))) ch[nc++] = ts[k];
                if (!nc) break;
                fp_tile_t c = ch[end ? nc - 1 : 0];
                fp_cand_t before = s->cands[0];
                const char *v = c.value != FP_NONE && *fp_s(&s->pk, c.value) ? fp_s(&s->pk, c.value) : NULL;
                r = fp_pick(s, fp_s(&s->pk, f[0].key), v, v ? NULL : fp_s(&s->pk, c.to));
                fp_cand_t *a = &s->cands[0];
                bool same = a->card == before.card && a->asking == before.asking && a->filled.n == before.filled.n;
                for (int k = 0; same && k < a->filled.n; k++) same = a->filled.slot[k] == before.filled.slot[k] && a->filled.val[k] == before.filled.val[k];
                if (r.kind == FP_FORM && same) {
                    printf("LOOP %s: '%s' tap '%s'\n", fp_s(&s->pk, s->trees[t].name), text,
                           c.label != FP_NONE ? fp_s(&s->pk, c.label) : "?");
                    bad++;
                    break;
                }
            }
        }
    return bad;
}

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: %s form.fpk script.txt\n", argv[0]); return 2; }
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 2;
    fseek(f, 0, SEEK_END); long n = ftell(f); rewind(f);
    void *blob = malloc(n);
    if (fread(blob, 1, n, f) != (size_t)n) return 2;
    fclose(f);
    static fp_sess_t s;
    if (!fp_sess_init(&s, blob, n)) { fprintf(stderr, "bad pack\n"); return 1; }
    fprintf(stderr, "%d trees, %ld bytes\n", s.ntrees, n);
    FILE *g = strcmp(argv[2], "-") ? fopen(argv[2], "r") : stdin;   /* "-": interactive (fake board) */
    if (!g) return 2;
    static char line[8192], out[65536];
    fp_screen_t sc;
    fp_screen_init(&sc, &s);
    int loops = 0, fuzzed = 0;
    while (fgets(line, sizeof line, g)) {
        line[strcspn(line, "\n")] = 0;
        char *p = strchr(line, ' ');
        if (!p) continue;
        *p++ = 0;
        if (!strcmp(line, "HINT")) { static char hint[160]; snprintf(hint, sizeof hint, "%s", p); sc.form_hint = hint; continue; }
        if (!strcmp(line, "CASE")) { fp_reset(&s); fp_screen_init(&sc, &s); printf("CASE %s\n", p); fflush(stdout); continue; }
        struct timespec t0, t1;
        clock_gettime(CLOCK_MONOTONIC, &t0);
        if (!strcmp(line, "SAY")) fp_screen_said(&sc, p, out, sizeof out);
        else if (!strcmp(line, "PICK")) {
            char *k = field(&p), *v = field(&p), *nd = field(&p);
            fp_screen_picked(&sc, k, v, nd, out, sizeof out);
        } else if (!strcmp(line, "SKIP")) fp_screen_skip(&sc, out, sizeof out);
        else if (!strcmp(line, "OPEN")) fp_screen_open(&sc, p, out, sizeof out);
        else if (!strcmp(line, "NEW")) fp_screen_new(&sc, out, sizeof out);
        else if (!strcmp(line, "AGAIN")) { char *r = field(&p); fp_screen_again(&sc, p, r ? atoi(r) : 0, out, sizeof out); }
        else if (!strcmp(line, "FUZZ")) { loops += fuzz(&s, p, 10); fuzzed++; continue; }
        else continue;
        clock_gettime(CLOCK_MONOTONIC, &t1);
        if (getenv("VFORM_TIME")) fprintf(stderr, "TIME %ld us %s %.60s\n", (t1.tv_sec - t0.tv_sec) * 1000000 + (t1.tv_nsec - t0.tv_nsec) / 1000, line, p);
        printf("%s\n", out);
        fflush(stdout);
    }
    if (fuzzed) printf("FUZZ %d sentences, %d loops\n", fuzzed, loops);
    if (s.rank_overflow) printf("WARNING rank queue overflowed %u times\n", s.rank_overflow);
    fp_sess_free(&s);
    return 0;
}
