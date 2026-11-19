/* fasr_decode.c - see fasr_decode.h. A line-for-line port of pipeline/speech/fasr/decode.py
 * (search, fit_words, nbest, fit_gap, reject_features, calibrated_reject). Where the Python relies
 * on dict insertion order (beam ties, the final n-best) the C keeps the same order. */
#include "fasr_decode.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

#define NEG (-1e30)
#define SEARCH_WORDS 64
#define GARB (-1)

static inline uint16_t r16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }
static inline uint32_t r32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
static inline double rd(const uint8_t *p) { double d; memcpy(&d, p, 8); return d; }

bool fd_open(fd_lex_t *x, const void *blob, size_t len) {
    const uint8_t *b = blob;
    if (len < 108 || memcmp(b, "LEX2", 4) || r16(b + 4) != 2) return false;
    x->b = b; x->len = len;
    x->ntok = r16(b + 6); x->nids = r32(b + 8); x->nnodes = r32(b + 12);
    x->blank = b[16]; x->sep = b[17];
    x->beam = (int)rd(b + 20); x->tok_prune = rd(b + 28); x->word_bonus = rd(b + 36); x->implicit_sep = rd(b + 44);
    x->garbage_enter = rd(b + 52); x->garbage_phone = rd(b + 60); x->reject_gap0 = rd(b + 68);
    x->reject_gap_scale = rd(b + 76); x->life_veto_conf = (int)r32(b + 84);
    x->words = b + r32(b + 88); x->cls = b + r32(b + 92); x->flags = b + r32(b + 96);
    x->bias = b + r32(b + 100); x->nodes = b + r32(b + 104);
    return x->ntok <= FD_MAX_TOK && x->beam <= FD_MAX_BEAM;
}

const char *fd_word(const fd_lex_t *x, uint16_t id) {
    if (!id || id >= x->nids) return NULL;
    uint32_t o = r32(x->words + 4 * id);
    return o && o < x->len ? (const char *)x->b + o : NULL;
}
static double bias_of(const fd_lex_t *x, int id) { return id > 0 && (uint32_t)id < x->nids ? rd(x->bias + 8 * id) : 0.0; }
static bool has_bias(const fd_lex_t *x, int id) { return bias_of(x, id) != 0.0; }
static int child(const fd_lex_t *x, int node, int tok) {
    const uint8_t *n = x->nodes + 12 * node;
    const uint8_t *c = x->b + r32(n + 4);
    for (int i = 0; i < n[1]; i++) if (r16(c + 8 * i) == tok) return (int)r32(c + 8 * i + 4);
    return -2;
}
static int node_nwords(const fd_lex_t *x, int node) { return r16(x->nodes + 12 * node + 2); }
static int node_word(const fd_lex_t *x, int node, int i) { return r16(x->b + r32(x->nodes + 12 * node + 8) + 2 * i); }

static double lse(double a, double b) {
    if (a < b) { double t = a; a = b; b = t; }
    return b <= NEG ? a : a + log1p(exp(b - a));
}

/* ---- word sequences: hash-consed (parent, word) chains, so equal tuples have equal ids ---- */
typedef struct { int32_t parent; uint16_t word; uint8_t len; } seq_t;
struct fd_ws {
    seq_t seq[FD_MAX_SEQ]; int nseq;
    int32_t sh[FD_SEQ_HASH];                         /* seq hash -> index + 1 */
    struct { int32_t seq; int32_t node; uint8_t last; double pb, pnb; } e[FD_MAX_ENT]; int ne;
    int32_t eh[FD_ENT_HASH];
    struct { int32_t seq; int32_t node; uint8_t last; double pb, pnb; } beam[FD_MAX_BEAM]; int nb;
};
static int seq_get(fd_ws_t *w, int parent, int word) {  /* interned (parent + word) */
    uint32_t h = ((uint32_t)(parent + 1) * 2654435761u) ^ ((uint32_t)word * 40503u);
    for (uint32_t i = h & (FD_SEQ_HASH - 1);; i = (i + 1) & (FD_SEQ_HASH - 1)) {
        int32_t k = w->sh[i];
        if (!k) {
            if (w->nseq >= FD_MAX_SEQ) return -2;    /* out of space: drop the extension */
            seq_t *s = &w->seq[w->nseq];
            s->parent = parent; s->word = (uint16_t)word; s->len = (uint8_t)(parent < 0 ? 1 : w->seq[parent].len + 1);
            w->sh[i] = ++w->nseq;
            return w->nseq - 1;
        }
        seq_t *s = &w->seq[k - 1];
        if (s->parent == parent && s->word == word) return k - 1;
    }
}
static int seq_len(const fd_ws_t *w, int s) { return s < 0 ? 0 : w->seq[s].len; }
static int seq_last(const fd_ws_t *w, int s) { return w->seq[s].word; }
static int seq_words(const fd_ws_t *w, int s, uint16_t *out) {
    int n = seq_len(w, s);
    for (int i = n - 1; i >= 0; i--, s = w->seq[s].parent) out[i] = w->seq[s].word;
    return n;
}

/* Rebuild the arena with only the chains the beams still use (ids change, equality does not). */
static void compact(fd_ws_t *w) {
    static uint16_t words[FD_MAX_BEAM][SEARCH_WORDS]; static int lens[FD_MAX_BEAM];
    for (int i = 0; i < w->nb; i++) lens[i] = seq_words(w, w->beam[i].seq, words[i]);
    w->nseq = 0; memset(w->sh, 0, sizeof w->sh);
    for (int i = 0; i < w->nb; i++) {
        int s = -1;
        for (int k = 0; k < lens[i]; k++) s = seq_get(w, s, words[i][k]);
        w->beam[i].seq = s;
    }
}

static void add(fd_ws_t *w, int seq, int node, int last, double pb, double pnb) {
    uint32_t h = ((uint32_t)(seq + 1) * 2654435761u) ^ ((uint32_t)(node + 2) * 97u) ^ ((uint32_t)last * 131071u);
    for (uint32_t i = h & (FD_ENT_HASH - 1);; i = (i + 1) & (FD_ENT_HASH - 1)) {
        int32_t k = w->eh[i];
        if (!k) {
            if (w->ne >= FD_MAX_ENT) return;
            w->e[w->ne].seq = seq; w->e[w->ne].node = node; w->e[w->ne].last = (uint8_t)last;
            w->e[w->ne].pb = pb; w->e[w->ne].pnb = pnb;
            w->eh[i] = ++w->ne;
            return;
        }
        if (w->e[k - 1].seq == seq && w->e[k - 1].node == node && w->e[k - 1].last == last) {
            w->e[k - 1].pb = lse(w->e[k - 1].pb, pb);
            w->e[k - 1].pnb = lse(w->e[k - 1].pnb, pnb);
            return;
        }
    }
}

/* _finish: ways to close the word in progress -> (seq, delta) pairs */
static int finish(const fd_lex_t *x, fd_ws_t *w, int seq, int node, int *out_seq, double *out_d, int max) {
    int n = 0;
    if (node == 0) { out_seq[0] = seq; out_d[0] = 0.0; return 1; }
    if (node == GARB) {
        if (seq >= 0 && seq_last(w, seq) == 0) { out_seq[0] = seq; out_d[0] = 0.0; return 1; }
        if (seq_len(w, seq) < SEARCH_WORDS) { int s = seq_get(w, seq, 0); if (s >= -1) { out_seq[0] = s; out_d[0] = 0.0; return 1; } }
        return 0;
    }
    if (seq_len(w, seq) >= SEARCH_WORDS) return 0;
    for (int i = 0; i < node_nwords(x, node) && n < max; i++) {
        int wd = node_word(x, node, i), s = seq_get(w, seq, wd);
        if (s < 0) continue;
        out_seq[n] = s; out_d[n] = x->word_bonus + bias_of(x, wd); n++;
    }
    return n;
}

static bool is_filler(const fd_lex_t *x, int id) { return id > 0 && (uint32_t)id < x->nids && (x->flags[id] & 1); }
static int cls_of(const fd_lex_t *x, int id) { return id >= 0 && (uint32_t)id < x->nids ? x->cls[id] : 0; }

/* fit_words: squeeze into ASR_MAX_WORDS slots (drop <unk>, then FILLER, then other class-0 words) */
static int fit_words(const fd_lex_t *x, const uint16_t *w, int n, uint16_t *out) {
    if (n <= FD_MAX_WORDS) { memcpy(out, w, 2 * n); return n; }
    uint16_t keep[SEARCH_WORDS]; int k = 0;
    for (int i = 0; i < n; i++) if (w[i]) keep[k++] = w[i];
    if (!k) keep[k++] = 0;
    for (int pass = 0; pass < 2; pass++) {
        for (int i = k - 1; k > FD_MAX_WORDS && i >= 0; i--) {
            int id = keep[i];
            bool drop = pass == 0 ? is_filler(x, id) : (cls_of(x, id) == 0 && id >= 10 && !has_bias(x, id));
            if (drop && !has_bias(x, id)) { memmove(keep + i, keep + i + 1, 2 * (k - i - 1)); k--; }
        }
    }
    if (k > FD_MAX_WORDS) k = FD_MAX_WORDS;
    memcpy(out, keep, 2 * k);
    return k;
}

/* numpy's pairwise sum (float64), so sums over frames round exactly like the Python's */
static double pairwise(const double *a, int n) {
    if (n < 8) { double s = 0.; for (int i = 0; i < n; i++) s += a[i]; return s; }
    if (n <= 128) {
        double r[8];
        for (int j = 0; j < 8; j++) r[j] = a[j];
        int i;
        for (i = 8; i < n - (n % 8); i += 8) for (int j = 0; j < 8; j++) r[j] += a[i + j];
        double res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
        for (; i < n; i++) res += a[i];
        return res;
    }
    int n2 = n / 2;
    n2 -= n2 % 8;
    return pairwise(a, n2) + pairwise(a + n2, n - n2);
}

/* Python round(x, 3) for the values nbest() reports */
static double round3(double v) { return nearbyint(v * 1000.0) / 1000.0; }

size_t fd_ws_size(void) { return sizeof(fd_ws_t); }

int fd_decode(const fd_lex_t *x, const float *logp, int T, const fd_calib_t *cal, void *wsv, fd_result_t *r) {
    fd_ws_t *w = wsv;
    memset(r, 0, sizeof *r);
    w->nseq = 0; memset(w->sh, 0, sizeof w->sh);
    int C = x->ntok, B = x->blank, SEP = x->sep;
    w->nb = 1; w->beam[0].seq = -1; w->beam[0].node = 0; w->beam[0].last = (uint8_t)SEP; w->beam[0].pb = 0.0; w->beam[0].pnb = NEG;
    int fs[64]; double fdl[64];
    for (int t = 0; t < T; t++) {
        const float *rowf = logp + (size_t)t * C;
        double row[FD_MAX_TOK];
        for (int c = 0; c < C; c++) row[c] = rowf[c];
        int cand[FD_MAX_TOK], nc = 0;
        for (int c = 0; c < C; c++) if (row[c] > x->tok_prune && c != B) cand[nc++] = c;
        w->ne = 0; memset(w->eh, 0, sizeof w->eh);
        for (int bi = 0; bi < w->nb; bi++) {
            int seq = w->beam[bi].seq, node = w->beam[bi].node, last = w->beam[bi].last;
            double pb = w->beam[bi].pb, pnb = w->beam[bi].pnb, tot = lse(pb, pnb);
            add(w, seq, node, last, tot + row[B], NEG);                       /* blank */
            bool last_in = false;
            for (int i = 0; i < nc; i++) if (cand[i] == last) last_in = true;
            if (last_in && pnb > NEG) add(w, seq, node, last, NEG, pnb + row[last]);   /* repeat */
            for (int i = 0; i < nc; i++) {
                int c = cand[i];
                double base = c != last ? lse(pb, pnb) : pb;
                if (base <= NEG) continue;
                double s = base + row[c];
                if (c == SEP) {
                    if (node == 0) continue;
                    int nf = finish(x, w, seq, node, fs, fdl, 64);
                    for (int k = 0; k < nf; k++) add(w, fs[k], 0, SEP, NEG, s + fdl[k]);
                    continue;
                }
                if (node == GARB) { add(w, seq, GARB, c, NEG, s + x->garbage_phone); continue; }
                int ch = child(x, node, c);
                if (ch >= 0) add(w, seq, ch, c, NEG, s);
                if (node == 0) add(w, seq, GARB, c, NEG, s + x->garbage_enter + x->garbage_phone);
                else {                                                        /* implicit boundary */
                    int ch0 = child(x, 0, c);
                    if (ch0 >= 0) {
                        int nf = finish(x, w, seq, node, fs, fdl, 64);
                        for (int k = 0; k < nf; k++) add(w, fs[k], ch0, c, NEG, s + fdl[k] + x->implicit_sep);
                    }
                }
            }
        }
        /* keep the best `beam`, sorted by -lse(pb, pnb), stable (insertion order on ties) */
        int keep[FD_MAX_BEAM]; double ks[FD_MAX_BEAM]; int nk = 0;
        for (int i = 0; i < w->ne; i++) {
            double sc = lse(w->e[i].pb, w->e[i].pnb);
            if (nk == x->beam && !(sc > ks[nk - 1])) continue;
            int j = nk < x->beam ? nk++ : nk - 1;
            while (j > 0 && sc > ks[j - 1]) { keep[j] = keep[j - 1]; ks[j] = ks[j - 1]; j--; }
            keep[j] = i; ks[j] = sc;
        }
        w->nb = nk;
        for (int i = 0; i < nk; i++) {
            int e = keep[i];
            w->beam[i].seq = w->e[e].seq; w->beam[i].node = w->e[e].node; w->beam[i].last = w->e[e].last;
            w->beam[i].pb = w->e[e].pb; w->beam[i].pnb = w->e[e].pnb;
        }
        compact(w);
    }
    /* final: close every beam, fit to 16 words, merge equal fitted tuples (first-seen order) */
    static struct { uint16_t w[FD_MAX_WORDS]; uint8_t n; double sc; int full; } fin[256];
    int nfin = 0;
    for (int bi = 0; bi < w->nb; bi++) {
        double tot = lse(w->beam[bi].pb, w->beam[bi].pnb);
        int nf = finish(x, w, w->beam[bi].seq, w->beam[bi].node, fs, fdl, 64);
        for (int k = 0; k < nf; k++) {
            uint16_t all[SEARCH_WORDS], f2[FD_MAX_WORDS];
            int na = seq_words(w, fs[k], all), n2 = fit_words(x, all, na, f2);
            double sc = tot + fdl[k];
            int at = -1;
            for (int q = 0; q < nfin; q++) if (fin[q].n == n2 && !memcmp(fin[q].w, f2, 2 * n2)) { at = q; break; }
            if (at < 0) {
                if (nfin >= 256) continue;
                at = nfin++; memcpy(fin[at].w, f2, 2 * n2); fin[at].n = (uint8_t)n2; fin[at].sc = NEG; fin[at].full = fs[k];
            } else if (sc > fin[at].sc) fin[at].full = fs[k];
            fin[at].sc = lse(fin[at].sc, sc);
        }
    }
    /* sorted by -score, stable */
    int ord[256];
    for (int i = 0; i < nfin; i++) {
        int j = i;
        while (j > 0 && fin[i].sc > fin[ord[j - 1]].sc) { ord[j] = ord[j - 1]; j--; }
        ord[j] = i;
    }
    /* nbest */
    double *best = malloc(sizeof(double) * (T > 0 ? T : 1));
    int speech = 0;
    for (int t = 0; t < T; t++) {
        const float *rw = logp + (size_t)t * C;
        int am = 0;
        for (int c = 1; c < C; c++) if (rw[c] > rw[am]) am = c;
        best[t] = rw[am];
        if (am != B) speech++;
    }
    if (!nfin) {
        r->n = 0; r->reject = r->reject_raw = 100; r->gap = 0.0; r->junk = 1.0;
    } else {
        double top = fin[ord[0]].sc, post[256], ps = 0.0;
        for (int i = 0; i < nfin; i++) post[i] = exp(fin[ord[i]].sc - top);
        for (int i = 0; i < nfin; i++) ps += post[i];
        for (int i = 0; i < nfin; i++) post[i] /= ps;
        double junk = 0.0;
        for (int i = 0; i < nfin; i++) {
            bool allz = true;
            for (int q = 0; q < fin[ord[i]].n; q++) if (fin[ord[i]].w[q]) allz = false;
            if (allz) junk += post[i];
        }
        double gap = pairwise(best, T) - top;
        gap = (gap > 0.0 ? gap : 0.0) / (speech > 1 ? speech : 1);
        double misfit = 1.0 / (1.0 + exp(-(gap - x->reject_gap0) / x->reject_gap_scale));
        r->n = nfin < FD_MAX_HYPS ? nfin : FD_MAX_HYPS;
        bool veto = false;
        for (int h = 0; h < r->n; h++) {
            const typeof(fin[0]) *f = &fin[ord[h]];
            r->h[h].len = f->n;
            r->h[h].p = post[h];
            for (int q = 0; q < f->n; q++) {
                double m = 0.0;
                for (int j = 0; j < nfin; j++) {
                    bool in = false;
                    for (int z = 0; z < fin[ord[j]].n; z++) if (fin[ord[j]].w[z] == f->w[q]) in = true;
                    if (in) m += post[j];
                }
                r->h[h].id[q] = f->w[q];
                r->h[h].conf[q] = (uint8_t)nearbyint(100.0 * (m < 1.0 ? m : 1.0));
                if (has_bias(x, f->w[q]) && r->h[h].conf[q] >= x->life_veto_conf) veto = true;
            }
        }
        r->reject_raw = (int)nearbyint(100.0 * (junk > misfit ? junk : misfit));
        r->junk = round3(junk); r->gap = round3(gap); r->veto = veto;   /* rounded as in nbest(): the reject features use these */
        uint16_t full[SEARCH_WORDS];
        int nfull = seq_words(w, fin[ord[0]].full, full);
        r->full_len = nfull; r->full_unk = 0;
        for (int i = 0; i < nfull; i++) r->full_unk += full[i] == 0;
        if (veto && r->reject_raw > 40) r->reject_raw = 40;
        r->reject = r->reject_raw;
    }
    if (cal) {                                       /* calibrated_reject: the canonical reject */
        double f[8];
        int n_ids = r->n ? r->h[0].len : 0;
        int n_full = r->n ? r->full_len : 0, unk_full = r->n ? r->full_unk : 0;
        double cs = 0;
        for (int i = 0; i < n_ids; i++) cs += r->h[0].conf[i];
        double *sb = malloc(sizeof(double) * (T > 0 ? T : 1));
        int ns = 0;
        for (int t = 0; t < T; t++) {
            const float *rw = logp + (size_t)t * C;
            if (exp((double)rw[B]) < 0.5) {
                double mx = NEG;
                for (int c = 0; c < C; c++) if (c != B && rw[c] > mx) mx = rw[c];
                sb[ns++] = mx;
            }
        }
        f[0] = r->n ? r->junk : 1.0;
        f[1] = n_full ? (double)unk_full / n_full : 1.0;
        f[2] = n_ids ? 1.0 - cs / (100.0 * n_ids) : 1.0;
        f[3] = n_ids ? 0.0 : 1.0;
        f[4] = T ? (double)ns / T : 0.0;
        f[5] = ns ? pairwise(sb, ns) / ns : -10.0;
        f[6] = n_full / (ns + 1.0);
        f[7] = r->n ? r->gap : 0.0;
        free(sb);
        double z = cal->bias;
        double dot = 0.0;
        for (int i = 0; i < 8; i++) dot += (f[i] - cal->mean[i]) / cal->std[i] * cal->weights[i];
        z += dot;
        double th = cal->threshold < 1e-6 ? 1e-6 : cal->threshold > 1 - 1e-6 ? 1 - 1e-6 : cal->threshold;
        z -= log(th / (1 - th));
        if (z < -50) z = -50;
        if (z > 50) z = 50;
        int rr = (int)nearbyint(100.0 / (1.0 + exp(-z)));
        r->reject = r->veto && rr > 40 ? 40 : rr;
    }
    free(best);
    return r->n;
}
