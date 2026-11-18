/* vform.c - see vform.h. Each function names the Python it ports (vr/intake.py, vr/form.py,
 * vr/live.py at 3d07632). Where the Python relies on dict / set order, the C keeps the same order:
 * slot-map order for slots and values, screen order for tiles, insertion order for filled blanks. */
#include "vform.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ------------------------------------------------------------------ pack */

static inline uint16_t rd16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }
static inline int16_t rds16(const uint8_t *p) { return (int16_t)rd16(p); }
static inline uint32_t rd32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }

bool fp_open(fp_pack_t *pk, const void *blob, size_t len) {
    const uint8_t *b = blob;
    if (len < 24 || memcmp(b, "FPK1", 4) || rd16(b + 4) != 1 || rd32(b + 20) != len) return false;
    pk->b = b; pk->len = len; pk->ntrees = rd16(b + 6); pk->nstr = rd32(b + 8);
    if (rd32(b + 12) + 4 * pk->nstr > len || rd32(b + 16) + 4u * pk->ntrees > len) return false;
    return true;
}

const char *fp_s(const fp_pack_t *pk, fp_str id) {
    if (id == FP_NONE || id >= pk->nstr) return NULL;
    return (const char *)pk->b + rd32(pk->b + rd32(pk->b + 12) + 4u * id);
}

int fp_find_str(const fp_pack_t *pk, const char *s) {
    for (uint32_t i = 0; i < pk->nstr; i++)
        if (!strcmp(fp_s(pk, (fp_str)i), s)) return (int)i;
    return -1;
}

#define S(id) fp_s(&s->pk, (id))
#define AT(off) (s->pk.b + (off))

/* records (layouts in tools/formpack.py) */
static const uint8_t *node(const fp_sess_t *s, int t, int i) { return s->trees[t].nodes + 32 * i; }
#define N_KEY(n) rd16((n) + 0)
#define N_LABEL(n) rd16((n) + 2)
#define N_QSLOT(n) rd16((n) + 4)
#define N_NCH(n) rd16((n) + 6)
#define N_NTILES(n) rd16((n) + 8)
#define N_NQCH(n) rd16((n) + 10)
#define N_NCITES(n) rd16((n) + 12)
#define N_CH(s, n, i) AT(rd32((n) + 16) + 8 * (i))
#define N_TILE(s, n, i) AT(rd32((n) + 20) + 8 * (i))
#define N_QCH(s, n, i) AT(rd32((n) + 24) + 8 * (i))
#define N_CITE(s, n, i) rd16(AT(rd32((n) + 28) + 2 * (i)))
/* CHOICE: to label to_node has_value kind_next */
#define C_TO(c) rd16(c)
#define C_NODE(c) rds16((c) + 4)
#define C_HASVAL(c) ((c)[6])
#define C_NEXT(c) ((c)[7])
/* TILE: label value to to_node */
#define T_LABEL(t) rd16(t)
#define T_VALUE(t) rd16((t) + 2)
#define T_TO(t) rd16((t) + 4)
#define T_NODE(t) rds16((t) + 6)
/* QCH: value to to_node */
#define Q_VALUE(q) rd16(q)
#define Q_TO(q) rd16((q) + 2)
#define Q_NODE(q) rds16((q) + 4)

static const uint8_t *slotrec(const fp_sess_t *s, int t, int i) { return s->trees[t].slots + 16 * i; }
#define SL_NAME(r) rd16(r)
#define SL_NVAL(r) rd16((r) + 2)
#define SL_NPRIO(r) rd16((r) + 4)
#define SL_VAL(s, r, i) AT(rd32((r) + 8) + 8 * (i))
#define SL_PRIO(s, r, i) rd16(AT(rd32((r) + 12) + 2 * (i)))
#define V_NAME(v) rd16(v)
#define V_NPH(v) rd16((v) + 2)
#define V_PH(s, v, i) rd16(AT(rd32((v) + 4) + 2 * (i)))
static const uint8_t *rule(const fp_sess_t *s, int t, int i) { return s->trees[t].rules + 12 * i; }
#define R_NIF(r) rd16(r)
#define R_NUNL(r) rd16((r) + 2)
#define R_GO(r) rd16((r) + 4)
#define R_GONODE(r) rds16((r) + 6)
#define R_PAIR(s, r, i) AT(rd32((r) + 8) + 4 * (i))

static bool truthy(const fp_sess_t *s, fp_str id) { return id != FP_NONE && S(id)[0]; }

/* ------------------------------------------------------------------ word sets */

static bool in(const char *w, const char *const *set) {
    for (; *set; set++) if (!strcmp(w, *set)) return true;
    return false;
}
/* vr.intake.NEGATION = vr.lexicon.NEGATION | {...} */
static const char *const NEGATION[] = {"not", "no", "can't", "cannot", "don't", "isn't", "won't", "without",
    "never", "didn't", "doesn't", "wasn't", "aren't", "haven't", "nothing", "none",
    "hasn't", "couldn't", "wouldn't", "shouldn't", "weren't", "ain't", "nobody", 0};
static const char *const HEDGE[] = {"if", "whether", "unsure", "maybe", "barely", "might", "check",
    "will", "gonna", "could", "about", "sure", "tell", 0};
static const char *const NOT_A_STATE[] = {"problem", "problems", "trouble", "troubles", "issue", "issues",
    "difficulty", "difficulties", "question", "questions", "advice", "tips", "rate", 0};
static const char *const CONTRAST[] = {"but", "now", "though", "although", "except", 0};
static const char *const CLAUSE[] = {"but", "now", "though", "although", "except",
    "and", "so", "because", "cause", "then", "while", "plus", "also", "since", 0};
static const char *const SUBJECT[] = {"she's", "he's", "it's", "they're", "i'm", "we're", "you're",
    "she", "he", "they", "we", 0};
static const char *const SEEING[] = {"seeing", "finding", "having", "getting", "feeling", "noticing", "showing", 0};
static const char *const FILLER[] = {"a", "an", "the", "or", "and", "of", "on", "in", "is", "it", "to", "my",
    "your", "i", "yes", "no", 0};
static const char *const COMMANDS[] = {"back", "again", "home", 0};
static const char *const YESNO[] = {"yes", "yeah", "yep", "no", "nope", "nah", 0};
static const char *const YES[] = {"yes", "yeah", "yep", 0};
static const char *const NO[] = {"no", "nope", "nah", 0};
static const char *const CONTRACT[][2] = {{"isnt", "isn't"}, {"cant", "can't"}, {"dont", "don't"},
    {"didnt", "didn't"}, {"doesnt", "doesn't"}, {"wont", "won't"}, {"wasnt", "wasn't"}, {"arent", "aren't"},
    {"havent", "haven't"}, {"hasnt", "hasn't"}, {"couldnt", "couldn't"}, {"wouldnt", "wouldn't"},
    {"shouldnt", "shouldn't"}, {"werent", "weren't"}, {"aint", "ain't"}, {"im", "i'm"}, {"ive", "i've"},
    {"hes", "he's"}, {"shes", "she's"}, {"thats", "that's"}, {"theyre", "they're"}, {"youre", "you're"},
    {"theres", "there's"}, {0, 0}};

static int number(const char *w) {           /* vr.intake.NUMBERS; -1 = not a number (or "to"/"for") */
    static const char *const T[][2] = {{"one", "0"}, {"1", "0"}, {"first", "0"}, {"two", "1"}, {"2", "1"},
        {"second", "1"}, {"three", "2"}, {"3", "2"}, {"third", "2"}, {"four", "3"}, {"4", "3"}, {"fourth", "3"}, {0, 0}};
    for (int i = 0; T[i][0]; i++) if (!strcmp(w, T[i][0])) return T[i][1][0] - '0';
    return -1;
}

/* ------------------------------------------------------------------ text */

/* vr.lexicon.tokenize: lower, ’ -> ', findall [a-z0-9]+(?:'[a-z]+)? ; then vr.intake.tokenize:
 * contractions typed without the apostrophe, and "going to" -> "gonna". */
static void lex_tokenize(const char *text, fp_toks_t *t) {
    char buf[512];
    size_t n = 0;
    for (const unsigned char *p = (const unsigned char *)text; *p && n < sizeof buf - 1; p++) {
        if (p[0] == 0xE2 && p[1] == 0x80 && p[2] == 0x99) { buf[n++] = '\''; p += 2; continue; }
        unsigned char c = *p;
        if (c >= 'A' && c <= 'Z') c += 32;
        buf[n++] = (c >= 0x80) ? ' ' : (char)c;   /* non-ASCII never matches [a-z0-9] */
    }
    buf[n] = 0;
    t->n = 0;
#define AL(c) (((c) >= 'a' && (c) <= 'z') || ((c) >= '0' && (c) <= '9'))
#define LT(c) ((c) >= 'a' && (c) <= 'z')
    for (size_t i = 0; i < n && t->n < FP_MAX_TOK;) {
        if (!AL(buf[i])) { i++; continue; }
        size_t j = i;
        while (j < n && AL(buf[j])) j++;
        if (j + 1 < n && buf[j] == '\'' && LT(buf[j + 1])) { j += 2; while (j < n && LT(buf[j])) j++; }
        size_t L = j - i < FP_WLEN - 1 ? j - i : FP_WLEN - 1;
        memcpy(t->w[t->n], buf + i, L); t->w[t->n][L] = 0; t->n++;
        i = j;
    }
}

void fp_tokenize(const char *text, fp_toks_t *t) {
    fp_toks_t r;
    lex_tokenize(text, &r);
    t->n = 0;
    for (int i = 0; i < r.n; i++) {
        const char *w = r.w[i];
        for (int k = 0; CONTRACT[k][0]; k++) if (!strcmp(w, CONTRACT[k][0])) { w = CONTRACT[k][1]; break; }
        if (!strcmp(w, "to") && t->n && !strcmp(t->w[t->n - 1], "going")) { strcpy(t->w[t->n - 1], "gonna"); continue; }
        strcpy(t->w[t->n++], w);
    }
}

void fp_stem(const char *w, bool verbs, char *out) {
    size_t L = strlen(w);
    memcpy(out, w, L + 1);
    if (in(w, NEGATION) || in(w, HEDGE) || strchr(w, '\'')) return;
#define ENDS(sfx) (L >= sizeof(sfx) - 1 && !memcmp(out + L - (sizeof(sfx) - 1), sfx, sizeof(sfx) - 1))
    if (L > 5 && ENDS("ly")) L -= 2;
    if (!verbs) {
        if (L > 3 && ENDS("s") && !ENDS("ss")) L--;
        out[L] = 0;
        return;
    }
    if (L > 5 && ENDS("ing")) L -= 3;
    else if (L > 4 && ENDS("ed") && !ENDS("eed")) L -= 2;
    else if (L > 3 && ENDS("s") && !ENDS("ss")) L--;
    if (L > 3 && out[L - 1] == out[L - 2] && !strchr("aeiouls", out[L - 1])) L--;
    out[L] = 0;
#undef ENDS
}

static void stems(const fp_toks_t *t, bool verbs, fp_toks_t *o) {
    o->n = t->n;
    for (int i = 0; i < t->n; i++) fp_stem(t->w[i], verbs, o->w[i]);
}

static bool neg_or_hedge(const char *w) { return in(w, NEGATION) || in(w, HEDGE); }

/* vr.intake._find: positions of the phrase in words (exact run first, else up to one filler word
 * between phrase words, never a negation/hedge). Returns a bitmask of positions, 0 = no hit. */
static uint64_t find(const fp_toks_t *w, const fp_toks_t *p) {
    int n = p->n;
    if (n == 0) return 0;                      /* (an empty phrase would crash the Python) */
    for (int i = 0; i + n <= w->n; i++) {
        int k = 0;
        while (k < n && !strcmp(w->w[i + k], p->w[k])) k++;
        if (k == n) return ((n == 64 ? ~0ull : (1ull << n) - 1)) << i;
    }
    if (n < 2) return 0;
    for (int k = 0; k < n; k++) if (neg_or_hedge(p->w[k])) return 0;
    const int gap = 1;
    for (int i = 0; i < w->n; i++) {
        if (strcmp(w->w[i], p->w[0])) continue;
        uint64_t pos = 1ull << i;
        int j = i, got = 1;
        for (int q = 1; q < n; q++) {
            int hit = -1, lim = j + 2 + gap < w->n ? j + 2 + gap : w->n;
            for (int k = j + 1; k < lim; k++) {
                if (strcmp(w->w[k], p->w[q])) continue;
                bool clean = true;
                for (int x = j + 1; x < k; x++) if (neg_or_hedge(w->w[x])) { clean = false; break; }
                if (clean) { hit = k; break; }
            }
            if (hit < 0) break;
            pos |= 1ull << hit; j = hit; got++;
        }
        if (got == n) return pos;
    }
    return 0;
}

static bool ing(const char *w) { size_t L = strlen(w); return L > 4 && !strcmp(w + L - 3, "ing"); }

/* vr.intake._scope: does a negation (neg) / hedge reach raw[i]? */
static bool scoped(const fp_toks_t *raw, int i, bool neg) {
    for (int j = i - 1; j > i - 1 - 3 && j >= 0; j--) {
        const char *w = raw->w[j];
        if (in(w, CLAUSE)) break;
        if (neg && in(w, SUBJECT)) break;
        if (neg && j == i - 1 && ing(w) && ing(raw->w[i]) && !in(w, SEEING)) break;
        if (in(w, neg ? NEGATION : HEDGE)) return true;
    }
    return false;
}

static int lowbit(uint64_t m) { int i = 0; while (!(m >> i & 1)) i++; return i; }
static int highbit(uint64_t m) { int i = 63; while (!(m >> i & 1)) i--; return i; }
static int popc(uint64_t m) { int n = 0; while (m) { m &= m - 1; n++; } return n; }

/* ------------------------------------------------------------------ filled */

static fp_str fget(const fp_filled_t *f, fp_str slot) {
    for (int i = 0; i < f->n; i++) if (f->slot[i] == slot) return f->val[i];
    return FP_NONE;
}
static void fset(fp_filled_t *f, fp_str slot, fp_str val) {
    for (int i = 0; i < f->n; i++) if (f->slot[i] == slot) { f->val[i] = val; return; }
    if (f->n < FP_MAX_FILLED) { f->slot[f->n] = slot; f->val[f->n] = val; f->n++; }
}
static bool fhas(const fp_filled_t *f, fp_str slot) { return fget(f, slot) != FP_NONE; }

/* ------------------------------------------------------------------ life rules */

static bool rule_fires(const fp_sess_t *s, const uint8_t *r, const fp_filled_t *f) {
    for (int i = 0; i < R_NIF(r); i++) { const uint8_t *p = R_PAIR(s, r, i); if (fget(f, rd16(p)) != rd16(p + 2)) return false; }
    for (int i = 0; i < R_NUNL(r); i++) { const uint8_t *p = R_PAIR(s, r, R_NIF(r) + i); if (fget(f, rd16(p)) == rd16(p + 2)) return false; }
    return true;
}
static bool rule_go_is(const uint8_t *r, fp_card card) {
    return card >= 0 ? R_GONODE(r) == card : R_GO(r) == FP_EXT_SID(card);
}
/* _life_slots (if-blanks) and decisive_slots (if + unless blanks) */
static bool life_slot(const fp_sess_t *s, int t, fp_str slot, bool with_unless) {
    for (int i = 0; i < s->trees[t].nrules; i++) {
        const uint8_t *r = rule(s, t, i);
        int n = R_NIF(r) + (with_unless ? R_NUNL(r) : 0);
        for (int k = 0; k < n; k++) if (rd16(R_PAIR(s, r, k)) == slot) return true;
    }
    return false;
}
static bool life_card(const fp_sess_t *s, int t, fp_card card) {
    for (int i = 0; i < s->trees[t].nrules; i++) if (rule_go_is(rule(s, t, i), card)) return true;
    return false;
}
static bool is_danger(const fp_sess_t *s, int t, fp_str slot, fp_str val) {
    for (int i = 0; i < s->trees[t].nrules; i++) {
        const uint8_t *r = rule(s, t, i);
        for (int k = 0; k < R_NIF(r); k++) { const uint8_t *p = R_PAIR(s, r, k); if (rd16(p) == slot && rd16(p + 2) == val) return true; }
    }
    return false;
}

/* ------------------------------------------------------------------ fill */

static int slot_index(const fp_sess_t *s, int t, fp_str slot) {
    for (int i = 0; i < s->trees[t].nslots; i++) if (SL_NAME(slotrec(s, t, i)) == slot) return i;
    return -1;
}

/* vr.intake.fill with decisive = decisive_slots(smap) (that is vr.intake._fill). */
static void fill_toks(const fp_sess_t *s, int t, const fp_toks_t *raw, fp_filled_t *out) {
    fp_toks_t words, plain, pw, p;
    stems(raw, true, &words);
    stems(raw, false, &plain);
    out->n = 0;
    uint64_t cuts = 0;
    for (int i = 0; i < words.n; i++) if (in(words.w[i], CONTRAST)) cuts |= 1ull << i;
    for (int si = 0; si < s->trees[t].nslots; si++) {
        const uint8_t *sl = slotrec(s, t, si);
        int nv = SL_NVAL(sl);
        int cv[64]; uint64_t cm[64]; int nc = 0;
        for (int vi = 0; vi < nv && vi < 64; vi++) {
            const uint8_t *v = SL_VAL(s, sl, vi);
            uint64_t covered = 0;
            for (int k = 0; k < V_NPH(v); k++) {
                fp_tokenize(S(V_PH(s, v, k)), &pw);
                uint64_t hit;
                if (pw.n == 1) { stems(&pw, false, &p); hit = find(&plain, &p); }
                else { stems(&pw, true, &p); hit = find(&words, &p); }
                if (!hit) continue;
                int first = lowbit(hit), last = highbit(hit);
                if (last + 1 < raw->n && in(raw->w[last + 1], NOT_A_STATE)) continue;
                if (pw.n > 1 && !strcmp(pw.w[pw.n - 1], "out") &&
                    (!strcmp(pw.w[pw.n - 2], "bleed") || !strcmp(pw.w[pw.n - 2], "bleeding") ||
                     !strcmp(pw.w[pw.n - 2], "bleeds") || !strcmp(pw.w[pw.n - 2], "bled")) &&
                    last + 1 < raw->n && !strcmp(raw->w[last + 1], "of")) continue;
                bool pneg = false, phedge = false;
                for (int q = 0; q < p.n; q++) { pneg |= in(p.w[q], NEGATION); phedge |= in(p.w[q], HEDGE); }
                if (scoped(raw, first, true) && !pneg) continue;
                if (scoped(raw, first, false) && !phedge) continue;
                covered |= hit;
            }
            if (covered) { cv[nc] = vi; cm[nc] = covered; nc++; }
        }
        if (!nc) continue;
        for (int c = 63; c >= 0; c--) {            /* later clause wins over an earlier one */
            if (!(cuts >> c & 1)) continue;
            int nl = 0; bool ok = true;
            for (int i = 0; i < nc; i++) {
                if (lowbit(cm[i]) > c) nl++;
                else if (highbit(cm[i]) >= c) ok = false;
            }
            if (nl && nl < nc && ok) {
                int k = 0;
                for (int i = 0; i < nc; i++) if (lowbit(cm[i]) > c) { cv[k] = cv[i]; cm[k] = cm[i]; k++; }
                nc = k;
                break;
            }
        }
        int top = 0;
        for (int i = 0; i < nc; i++) if (popc(cm[i]) > top) top = popc(cm[i]);
        /* order = priority + the other values in file order; tied sorted by order.index */
        int nprio = SL_NPRIO(sl), best = -1, best_rank = 1 << 30, ntied = 0;
        for (int i = 0; i < nc; i++) {
            if (popc(cm[i]) != top) continue;
            ntied++;
            fp_str vn = V_NAME(SL_VAL(s, sl, cv[i]));
            int rank = -1;
            for (int k = 0; k < nprio; k++) if (SL_PRIO(s, sl, k) == vn) { rank = k; break; }
            if (rank < 0) {
                rank = nprio;
                for (int k = 0; k < cv[i]; k++) {
                    fp_str o = V_NAME(SL_VAL(s, sl, k));
                    bool inp = false;
                    for (int q = 0; q < nprio; q++) if (SL_PRIO(s, sl, q) == o) inp = true;
                    if (!inp) rank++;
                }
            }
            if (rank < best_rank) { best_rank = rank; best = cv[i]; }
        }
        if (ntied > 1 && !life_slot(s, t, SL_NAME(sl), true)) continue;   /* two equally strong answers: ask */
        fset(out, SL_NAME(sl), V_NAME(SL_VAL(s, sl, best)));
    }
}

void fp_fill(const fp_sess_t *s, int t, const char *text, fp_filled_t *out) {
    fp_toks_t raw;
    fp_tokenize(text, &raw);
    fill_toks(s, t, &raw, out);
}

/* vr.intake.SOUNDALIKE */
static const char *soundalike(const char *w) {
    static const char *const P[][2] = {{"breathing", "bleeding"}, {"breathe", "bleed"}, {"breath", "blood"}, {0, 0}};
    for (int i = 0; P[i][0]; i++) {
        if (!strcmp(w, P[i][0])) return P[i][1];
        if (!strcmp(w, P[i][1])) return P[i][0];
    }
    return NULL;
}
static bool tok_has(const fp_toks_t *t, const char *w) {
    for (int i = 0; i < t->n; i++) if (!strcmp(t->w[i], w)) return true;
    return false;
}

/* vr.intake.fill_nbest */
static void fill_nbest(const fp_sess_t *s, int t, const char *const *hyps, int nh, fp_filled_t *out) {
    out->n = 0;
    if (nh <= 0) return;
    fp_fill(s, t, hyps[0], out);
    fp_toks_t top, h;
    fp_tokenize(hyps[0], &top);
    for (int i = 1; i < nh; i++) {
        fp_tokenize(hyps[i], &h);
        bool twin = false;
        for (int k = 0; k < h.n && !twin; k++) {
            if (tok_has(&top, h.w[k])) continue;   /* new = words of h not in the top guess */
            const char *a = soundalike(h.w[k]);
            if (a && tok_has(&top, a)) twin = true;
        }
        if (twin) continue;
        fp_filled_t f;
        fill_toks(s, t, &h, &f);
        for (int k = 0; k < f.n; k++)
            if (!fhas(out, f.slot[k]) && is_danger(s, t, f.slot[k], f.val[k])) fset(out, f.slot[k], f.val[k]);
    }
}

/* ------------------------------------------------------------------ walk */

/* vr.intake._answer: the choice a filled value takes, climbing `implies`. Returns the qch record or NULL. */
static const uint8_t *q_answer(const fp_sess_t *s, int t, const uint8_t *n, fp_str v) {
    fp_str seen[16]; int ns = 0;
    fp_str slot = N_QSLOT(n);
    while (v != FP_NONE && truthy(s, v)) {
        for (int i = 0; i < ns; i++) if (seen[i] == v) return NULL;
        for (int i = 0; i < N_NQCH(n); i++) if (Q_VALUE(N_QCH(s, n, i)) == v) return N_QCH(s, n, i);
        if (ns < 16) seen[ns++] = v; else return NULL;
        fp_str nv = FP_NONE;
        for (int i = 0; i < s->trees[t].nimplies; i++) {
            const uint8_t *im = s->trees[t].implies + 8 * i;
            if (rd16(im) == slot && rd16(im + 2) == v) { nv = rd16(im + 4); break; }
        }
        v = nv;
    }
    return NULL;
}

/* vr.intake._leads: does some question below `start` already have its blank answered? */
static bool leads(const fp_sess_t *s, int t, int start, const fp_filled_t *f) {
    int16_t todo[1024]; int nt = 0;
    uint8_t seen[FP_MAX_NODES / 8] = {0}; int nseen = 0;
    todo[nt++] = (int16_t)start;
    while (nt && nseen < 12 * 8) {
        int k = todo[--nt];
        if (k < 0 || (seen[k >> 3] >> (k & 7) & 1)) continue;
        seen[k >> 3] |= 1 << (k & 7); nseen++;
        const uint8_t *n = node(s, t, k);
        if (N_QSLOT(n) != FP_NONE) {
            const uint8_t *q = q_answer(s, t, n, fget(f, N_QSLOT(n)));
            if (q && truthy(s, Q_TO(q))) return true;
        }
        for (int i = 0; i < N_NCH(n) && nt < 1024; i++) {
            const uint8_t *c = N_CH(s, n, i);
            if (C_NEXT(c)) todo[nt++] = C_NODE(c);
        }
    }
    return false;
}

/* vr.intake.walk: follow the tree from its root (start < 0) or from `start`, taking every answer the
 * blanks give. Returns the card to show; *asking = the blank it asks or FP_NONE. */
static fp_card walk(const fp_sess_t *s, int t, const fp_filled_t *f, int start, fp_str *asking) {
    const fp_tree_t *tr = &s->trees[t];
    int nd = start >= 0 ? start : tr->root;
    *asking = FP_NONE;
    if (start < 0) {
        for (int i = 0; i < tr->nrules; i++) {
            const uint8_t *r = rule(s, t, i);
            if (!rule_fires(s, r, f)) continue;
            if (R_GONODE(r) < 0) return FP_EXT(R_GO(r));   /* another tree's card: open it */
            nd = R_GONODE(r);
            break;
        }
    }
    uint8_t seen[FP_MAX_NODES / 8] = {0};
    while (!(seen[nd >> 3] >> (nd & 7) & 1)) {
        seen[nd >> 3] |= 1 << (nd & 7);
        const uint8_t *n = node(s, t, nd);
        int target = -1;
        if (N_QSLOT(n) != FP_NONE) {
            const uint8_t *q = q_answer(s, t, n, fget(f, N_QSLOT(n)));
            if (q && truthy(s, Q_TO(q)) && Q_NODE(q) < 0 && strchr(S(Q_TO(q)), '/'))
                return FP_EXT(Q_TO(q));                     /* the answer leads to another tree's card */
            if (q && truthy(s, Q_TO(q))) target = Q_NODE(q);
            if (target < 0) {
                int hit = -1, nh = 0;
                for (int i = 0; i < N_NCH(n); i++) {
                    const uint8_t *c = N_CH(s, n, i);
                    if (C_HASVAL(c) || !C_NEXT(c) || C_NODE(c) < 0) continue;
                    if (C_NODE(c) == hit) continue;
                    if (leads(s, t, C_NODE(c), f)) {
                        if (nh && hit != C_NODE(c)) { nh = 2; continue; }
                        hit = C_NODE(c); nh = 1;
                    }
                }
                target = nh == 1 ? hit : -1;
            }
            if (target < 0) { *asking = N_QSLOT(n); return nd; }
        } else {
            int hit = -1, nh = 0;
            for (int i = 0; i < N_NCH(n); i++) {
                const uint8_t *c = N_CH(s, n, i);
                if (!C_NEXT(c) || C_NODE(c) < 0) continue;
                if (leads(s, t, C_NODE(c), f)) {
                    if (nh && hit != C_NODE(c)) nh = 2;
                    else if (!nh) { hit = C_NODE(c); nh = 1; }
                }
            }
            if (nh != 1) return nd;
            target = hit;
        }
        nd = target;
    }
    return nd;
}

static void advance(fp_sess_t *s, fp_cand_t *c, int start) { c->card = walk(s, c->tree, &c->filled, start, &c->asking); }

/* ------------------------------------------------------------------ tiles */

int fp_tiles(const fp_sess_t *s, int t, int nd, fp_tile_t *out, int max) {
    const uint8_t *n = node(s, t, nd);
    int k = 0;
    for (int i = 0; i < N_NTILES(n) && k < max; i++, k++) {
        const uint8_t *r = N_TILE(s, n, i);
        out[k] = (fp_tile_t){T_LABEL(r), T_VALUE(r), T_TO(r), T_NODE(r)};
    }
    return k;
}
fp_str fp_node_key(const fp_sess_t *s, int t, int nd) { return N_KEY(node(s, t, nd)); }
fp_str fp_node_label(const fp_sess_t *s, int t, int nd) { return N_LABEL(node(s, t, nd)); }
int fp_node_cites(const fp_sess_t *s, int t, int nd, fp_str *out, int max) {
    const uint8_t *n = node(s, t, nd);
    int k = 0;
    for (; k < N_NCITES(n) && k < max; k++) out[k] = N_CITE(s, n, k);
    return k;
}

#define MAXT 32

/* ------------------------------------------------------------------ answer_turn */

static bool lower_eq(const char *v, const char *w) {   /* v.strip().lower() == w */
    while (*v == ' ' || *v == '\t' || *v == '\n' || *v == '\r') v++;
    size_t L = strlen(v);
    while (L && (v[L - 1] == ' ' || v[L - 1] == '\t' || v[L - 1] == '\n' || v[L - 1] == '\r')) L--;
    if (L != strlen(w)) return false;
    for (size_t i = 0; i < L; i++) { char c = v[i]; if (c >= 'A' && c <= 'Z') c += 32; if (c != w[i]) return false; }
    return true;
}

/* vr.intake.answer_turn. Returns the picked tile index or -1; *f is updated. */
static int answer_turn(const fp_sess_t *s, int t, const char *text, int card, fp_str asking, fp_filled_t *f) {
    fp_toks_t words, lab;
    fp_tokenize(text, &words);
    if (!words.n) return -1;
    if (words.n <= 2 && in(words.w[words.n - 1], COMMANDS)) return -1;
    fp_tile_t ts[MAXT];
    int nt = fp_tiles(s, t, card, ts, MAXT), pick = -1;
    int num = -2; bool one = true;
    for (int i = 0; i < words.n; i++) {
        int v = number(words.w[i]);
        if (v < 0) continue;
        if (num == -2) num = v; else if (num != v) one = false;
    }
    if (num >= 0 && one && words.n <= 3 && num < nt) {
        pick = num;
    } else {
        fp_filled_t heard;
        fill_toks(s, t, &words, &heard);
        for (int k = 0; k < heard.n; k++) if (!fhas(f, heard.slot[k])) fset(f, heard.slot[k], heard.val[k]);
        fp_str hv = asking != FP_NONE ? fget(&heard, asking) : FP_NONE;
        bool yesno = words.n <= 3;
        for (int i = 0; i < words.n && yesno; i++) if (!in(words.w[i], YESNO)) yesno = false;
        if (hv != FP_NONE) {
            const uint8_t *n = node(s, t, card);
            const uint8_t *q = N_QSLOT(n) != FP_NONE ? q_answer(s, t, n, hv) : NULL;
            fp_str target = q ? Q_TO(q) : FP_NONE;
            for (int i = 0; i < nt && target != FP_NONE; i++) if (ts[i].to == target) { pick = i; break; }
        } else if (yesno) {
            bool y = false, no = false;
            for (int i = 0; i < words.n; i++) { y |= in(words.w[i], YES); no |= in(words.w[i], NO); }
            if (!(y && no)) {
                const char *yn = y ? "yes" : "no";
                for (int i = 0; i < nt && pick < 0; i++)
                    if (lower_eq(ts[i].value == FP_NONE ? "" : S(ts[i].value), yn)) pick = i;
                for (int i = 0; i < nt && pick < 0; i++) {
                    fp_tokenize(ts[i].label == FP_NONE ? "" : S(ts[i].label), &lab);
                    if (lab.n && !strcmp(lab.w[0], yn)) pick = i;
                }
            }
        }
        if (pick < 0) {                             /* the tile's own words: "the leg one" */
            int hits = 0, h = -1;
            for (int i = 0; i < nt; i++) {
                fp_tokenize(ts[i].label == FP_NONE ? "" : S(ts[i].label), &lab);
                int nonfill = 0; bool sub = true;
                for (int k = 0; k < lab.n; k++) {
                    if (in(lab.w[k], FILLER)) continue;
                    nonfill++;
                    if (!tok_has(&words, lab.w[k])) sub = false;
                }
                if (nonfill && sub) { hits++; if (h < 0) h = i; }
            }
            pick = hits == 1 ? h : -1;
        }
    }
    if (pick >= 0 && asking != FP_NONE && truthy(s, ts[pick].value)) fset(f, asking, ts[pick].value);
    return pick;
}

/* ------------------------------------------------------------------ snap */

/* difflib.SequenceMatcher(None, a, b).ratio() for short words (no junk, no autojunk below 200). */
static int longest(const char *a, int alo, int ahi, const char *b, int blo, int bhi, int *bi, int *bj) {
    int besti = alo, bestj = blo, bestsize = 0;
    int j2len[FP_WLEN + 1] = {0}, nj[FP_WLEN + 1];
    for (int i = alo; i < ahi; i++) {
        memset(nj, 0, sizeof nj);
        for (int j = blo; j < bhi; j++) {
            if (b[j] != a[i]) continue;
            int k = (j > 0 ? j2len[j - 1] : 0) + 1;
            nj[j] = k;
            if (k > bestsize) { besti = i - k + 1; bestj = j - k + 1; bestsize = k; }
        }
        memcpy(j2len, nj, sizeof nj);
    }
    while (besti > alo && bestj > blo && a[besti - 1] == b[bestj - 1]) { besti--; bestj--; bestsize++; }
    while (besti + bestsize < ahi && bestj + bestsize < bhi && a[besti + bestsize] == b[bestj + bestsize]) bestsize++;
    *bi = besti; *bj = bestj;
    return bestsize;
}
static int matches(const char *a, int alo, int ahi, const char *b, int blo, int bhi) {
    int i, j, k = longest(a, alo, ahi, b, blo, bhi, &i, &j);
    if (!k) return 0;
    int m = k;
    if (alo < i && blo < j) m += matches(a, alo, i, b, blo, j);
    if (i + k < ahi && j + k < bhi) m += matches(a, i + k, ahi, b, j + k, bhi);
    return m;
}
double fp_ratio(const char *a, const char *b) {
    int la = (int)strlen(a), lb = (int)strlen(b);
    if (la + lb == 0) return 1.0;
    return 2.0 * matches(a, 0, la, b, 0, lb) / (la + lb);
}

/* vr.form.snap: a near word snaps to the one answer it is clearly closest to. Returns the value or FP_NONE. */
static fp_str snap(const fp_sess_t *s, const char *text, const fp_cand_t *c) {
    if (c->asking == FP_NONE || life_slot(s, c->tree, c->asking, false)) return FP_NONE;
    fp_toks_t heard, words, w;
    fp_tokenize(text, &words);
    heard.n = 0;
    for (int i = 0; i < words.n; i++)
        if (strlen(words.w[i]) >= 4 && !in(words.w[i], FILLER)) strcpy(heard.w[heard.n++], words.w[i]);
    fp_tile_t ts[MAXT];
    int nt = fp_tiles(s, c->tree, c->card, ts, MAXT);
    fp_str bv[MAXT]; double bs[MAXT]; int nb = 0;
    int si = slot_index(s, c->tree, c->asking);
    for (int i = 0; i < nt; i++) {
        fp_str v = ts[i].value;
        if (!truthy(s, v)) continue;
        /* the candidate words: label words + the phrase words of this value (a set: order is irrelevant to the max) */
        char cw[64][FP_WLEN]; int ncw = 0;
        fp_tokenize(ts[i].label == FP_NONE ? "" : S(ts[i].label), &w);
        for (int k = 0; k < w.n && ncw < 64; k++) strcpy(cw[ncw++], w.w[k]);
        if (si >= 0) {
            const uint8_t *sl = slotrec(s, c->tree, si);
            for (int vi = 0; vi < SL_NVAL(sl); vi++) {
                const uint8_t *vr = SL_VAL(s, sl, vi);
                if (V_NAME(vr) != v) continue;
                for (int ph = 0; ph < V_NPH(vr); ph++) {
                    fp_tokenize(S(V_PH(s, vr, ph)), &w);
                    for (int k = 0; k < w.n && ncw < 64; k++) strcpy(cw[ncw++], w.w[k]);
                }
            }
        }
        for (int h = 0; h < heard.n; h++)
            for (int k = 0; k < ncw; k++) {
                if (strlen(cw[k]) < 4 || in(cw[k], FILLER)) continue;
                double r = fp_ratio(heard.w[h], cw[k]);
                int at = -1;
                for (int q = 0; q < nb; q++) if (bv[q] == v) at = q;
                if (r > (at < 0 ? 0.0 : bs[at])) {
                    if (at < 0) { at = nb++; bv[at] = v; }
                    bs[at] = r;
                }
            }
    }
    if (!nb) return FP_NONE;
    int top = 0, second = -1;                       /* sorted by -score, stable */
    for (int q = 1; q < nb; q++) if (bs[q] > bs[top]) top = q;
    for (int q = 0; q < nb; q++) if (q != top && (second < 0 || bs[q] > bs[second])) second = q;
    if (bs[top] < 0.80 || (nb > 1 && bs[top] - bs[second] < 0.10)) return FP_NONE;
    return bv[top];
}

/* ------------------------------------------------------------------ session */

struct fp_rank_ws {
    struct { int32_t parent; fp_card card; fp_str slot, val, asking; uint8_t n, plain; double ev; } q[8192];
    uint64_t seen[4096];
    struct { uint8_t cand; fp_card card; double score; int order; bool life; } out[512];
};

bool fp_sess_init(fp_sess_t *s, const void *blob, size_t len) {
    memset(s, 0, sizeof *s);
    if (!fp_open(&s->pk, blob, len)) return false;
    s->ntrees = s->pk.ntrees < FP_MAX_TREES ? s->pk.ntrees : FP_MAX_TREES;
    for (int i = 0; i < s->ntrees; i++) {
        const uint8_t *r = s->pk.b + rd32(s->pk.b + rd32(s->pk.b + 16) + 4 * i);
        fp_tree_t *t = &s->trees[i];
        t->rec = r; t->name = rd16(r); t->title = rd16(r + 2); t->root = rd16(r + 4); t->nnodes = rd16(r + 6);
        t->nslots = rd16(r + 8); t->nrules = rd16(r + 10); t->nimplies = rd16(r + 12); t->nlifew = rd16(r + 14);
        t->nodes = AT(rd32(r + 16)); t->slots = AT(rd32(r + 20)); t->rules = AT(rd32(r + 24));
        t->implies = AT(rd32(r + 28)); t->lifew = AT(rd32(r + 32));
        if (t->nnodes > FP_MAX_NODES || !t->nnodes) return false;
    }
    s->ws = malloc(sizeof *s->ws);
    return s->ws != NULL;
}

void fp_sess_free(fp_sess_t *s) { free(s->ws); s->ws = NULL; }
void fp_reset(fp_sess_t *s) { s->ncands = 0; }

int fp_tree_by_name(const fp_sess_t *s, const char *name) {
    for (int i = 0; i < s->ntrees; i++) if (!strcmp(S(s->trees[i].name), name)) return i;
    return -1;
}
int fp_node_by_key(const fp_sess_t *s, int t, const char *key) {
    for (int i = 0; i < s->trees[t].nnodes; i++) if (!strcmp(S(N_KEY(node(s, t, i))), key)) return i;
    return -1;
}

bool fp_resolve(const fp_sess_t *s, int t, fp_card card, int *ot, int *on) {
    if (card >= 0) { *ot = t; *on = card; return true; }
    const char *k = S(FP_EXT_SID(card)), *sl = strchr(k, '/');
    if (!sl) return false;
    char name[96];
    size_t L = (size_t)(sl - k) < sizeof name - 1 ? (size_t)(sl - k) : sizeof name - 1;
    memcpy(name, k, L); name[L] = 0;
    int tt = fp_tree_by_name(s, name);
    if (tt < 0) return false;
    int nn = fp_node_by_key(s, tt, sl + 1);
    if (nn < 0) return false;
    *ot = tt; *on = nn;
    return true;
}

/* vr.live.keep_trees */
int fp_keep_trees(fp_sess_t *s, const char *const *hyps, int nh, int k) {
    int sc[FP_MAX_TREES], idx[FP_MAX_TREES], n = 0;
    for (int t = 0; t < s->ntrees; t++) {
        fp_filled_t f;
        fill_nbest(s, t, hyps, nh, &f);
        if (!f.n) continue;
        int v = 0;
        for (int i = 0; i < f.n; i++) v += life_slot(s, t, f.slot[i], false) ? 2 : 1;
        sc[n] = v; idx[n] = t; n++;
    }
    for (int i = 1; i < n; i++)                      /* sort by score desc, then tree order (stable) */
        for (int j = i; j > 0 && sc[j] > sc[j - 1]; j--) {
            int a = sc[j]; sc[j] = sc[j - 1]; sc[j - 1] = a;
            a = idx[j]; idx[j] = idx[j - 1]; idx[j - 1] = a;
        }
    if (k > FP_MAX_CANDS) k = FP_MAX_CANDS;
    if (n > k) n = k;
    int tot = 0;
    for (int i = 0; i < n; i++) tot += sc[i];
    if (!tot) tot = 1;
    s->ncands = n;
    for (int i = 0; i < n; i++) {
        fp_cand_t *c = &s->cands[i];
        memset(c, 0, sizeof *c);
        c->tree = (uint8_t)idx[i]; c->p = (double)sc[i] / tot; c->card = s->trees[idx[i]].root; c->asking = FP_NONE;
    }
    return n;
}

static fp_result_t res(int kind, int tree, fp_card card) { return (fp_result_t){kind, (uint8_t)tree, card}; }

/* vr.form._own_evidence + split_tabs */
static bool own_evidence(const fp_sess_t *s, int ci) {
    const fp_cand_t *c = &s->cands[ci];
    for (int k = 0; k < c->filled.n; k++) {
        bool theirs = false;
        for (int o = 0; o < s->ncands && !theirs; o++)
            if (o != ci && slot_index(s, s->cands[o].tree, c->filled.slot[k]) >= 0) theirs = true;
        if (!theirs) return true;
    }
    return false;
}
int fp_split_tabs(const fp_sess_t *s, fp_tab_t *tabs, int max) {
    int own[FP_MAX_CANDS], nown = 0;
    for (int i = 0; i < s->ncands; i++) if (s->ncands > 1 && own_evidence(s, i)) own[nown++] = i;
    if (max < 1) return 0;
    if (nown < 2) {
        tabs[0].n = (uint8_t)s->ncands;
        for (int i = 0; i < s->ncands; i++) tabs[0].c[i] = (uint8_t)i;
        return 1;
    }
    int n = 0;
    for (int i = 0; i < nown && n < 5 && n < max; i++) { tabs[n].n = 1; tabs[n].c[0] = (uint8_t)own[i]; n++; }
    for (int i = 0; i < s->ncands; i++) {
        bool o = false;
        for (int k = 0; k < nown; k++) if (own[k] == i) o = true;
        if (!o) tabs[0].c[tabs[0].n++] = (uint8_t)i;
    }
    return n;
}

void fp_keep_tab(fp_sess_t *s, const fp_tab_t *tab) {
    fp_cand_t keep[FP_MAX_CANDS];
    for (int i = 0; i < tab->n; i++) keep[i] = s->cands[tab->c[i]];
    for (int i = 0; i < tab->n; i++) s->cands[i] = keep[i];
    s->ncands = tab->n;
}

static int rule_hit(const fp_sess_t *s, const fp_cand_t *c) {
    for (int i = 0; i < s->trees[c->tree].nrules; i++) if (rule_fires(s, rule(s, c->tree, i), &c->filled)) return i;
    return -1;
}

static fp_card rule_card(const fp_sess_t *s, int t, int i) {
    const uint8_t *r = rule(s, t, i);
    return R_GONODE(r) >= 0 ? R_GONODE(r) : FP_EXT(R_GO(r));
}

/* vr.form.first_sentence */
fp_result_t fp_first_sentence(fp_sess_t *s, const char *const *hyps, int nh, bool multi) {
    for (int i = 1; i < s->ncands; i++)              /* sort by -p, stable */
        for (int j = i; j > 0 && s->cands[j].p > s->cands[j - 1].p; j--) {
            fp_cand_t tmp = s->cands[j]; s->cands[j] = s->cands[j - 1]; s->cands[j - 1] = tmp;
        }
    for (int i = 0; i < s->ncands; i++) {
        fp_cand_t *c = &s->cands[i];
        fill_nbest(s, c->tree, hyps, nh, &c->filled);
        strncpy(c->heard, nh ? hyps[0] : "", FP_HEARD - 1); c->heard[FP_HEARD - 1] = 0;
        advance(s, c, -1);
        if (c->card < 0) return res(FP_OPEN, c->tree, c->card);
    }
    for (int i = 0; i < s->ncands; i++)
        if (rule_hit(s, &s->cands[i]) >= 0) return res(FP_OPEN, s->cands[i].tree, s->cands[i].card);
    fp_tab_t tabs[8];
    if (multi && fp_split_tabs(s, tabs, 8) > 1) return res(FP_TABS, 0, 0);
    if (s->ncands && (s->cands[0].p >= 0.80 || s->ncands == 1)) s->ncands = 1;
    if (s->ncands == 1) {
        fp_cand_t *c = &s->cands[0];
        if (c->asking == FP_NONE && c->card != s->trees[c->tree].root && !life_card(s, c->tree, c->card))
            return res(FP_OPEN, c->tree, c->card);
    }
    return res(FP_FORM, 0, 0);
}

/* ------------------------------------------------------------------ ranking */

static uint64_t mix(uint64_t x) {
    x += 0x9E3779B97F4A7C15ull;
    x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9ull;
    x = (x ^ (x >> 27)) * 0x94D049BB133111EBull;
    return x ^ (x >> 31);
}
static uint64_t state_hash(fp_card card, fp_str asking, const fp_filled_t *f) {
    uint64_t h = 0;
    for (int i = 0; i < f->n; i++) h += mix((uint64_t)f->slot[i] << 16 | f->val[i]);
    return mix(h ^ mix((uint64_t)(uint32_t)card << 16 | asking)) | 1;
}
static bool seen_add(fp_rank_ws_t *ws, uint64_t h) {   /* true if newly added */
    for (uint32_t i = (uint32_t)h & 4095;; i = (i + 1) & 4095) {
        if (ws->seen[i] == h) return false;
        if (!ws->seen[i]) { ws->seen[i] = h; return true; }
    }
}

typedef struct { int n; char w[FP_MAX_TOK][FP_WLEN]; } wset_t;
static void wset_stems(const char *text, wset_t *o) {   /* {stem(w) for w in tokenize(text)} - FILLER */
    fp_toks_t t; char st[FP_WLEN];
    fp_tokenize(text, &t);
    o->n = 0;
    for (int i = 0; i < t.n; i++) {
        fp_stem(t.w[i], true, st);
        if (in(st, FILLER)) continue;
        bool dup = false;
        for (int k = 0; k < o->n; k++) if (!strcmp(o->w[k], st)) dup = true;
        if (!dup) strcpy(o->w[o->n++], st);
    }
}
/* vr.form._soft: share of a phrase's (or the tile label's) words that were heard */
static double soft(const fp_sess_t *s, const fp_cand_t *c, const wset_t *heard, fp_str slot, fp_str value, fp_str label) {
    double best = 0.0;
    wset_t pw;
    int si = slot_index(s, c->tree, slot);
    if (si >= 0) {
        const uint8_t *sl = slotrec(s, c->tree, si);
        for (int vi = 0; vi < SL_NVAL(sl); vi++) {
            const uint8_t *v = SL_VAL(s, sl, vi);
            if (V_NAME(v) != value) continue;
            for (int k = 0; k < V_NPH(v); k++) {
                wset_stems(S(V_PH(s, v, k)), &pw);
                if (!pw.n) continue;
                int m = 0;
                for (int a = 0; a < pw.n; a++) for (int b = 0; b < heard->n; b++) if (!strcmp(pw.w[a], heard->w[b])) { m++; break; }
                double r = (double)m / pw.n;
                if (r > best) best = r;
            }
            break;
        }
    }
    wset_stems(label == FP_NONE ? "" : S(label), &pw);
    if (pw.n) {
        int m = 0;
        for (int a = 0; a < pw.n; a++) for (int b = 0; b < heard->n; b++) if (!strcmp(pw.w[a], heard->w[b])) { m++; break; }
        double r = (double)m / pw.n;
        if (r > best) best = r;
    }
    return best;
}

static void rebuild(const fp_rank_ws_t *ws, int32_t at, const fp_filled_t *base, fp_filled_t *f) {
    int32_t chain[64]; int n = 0;
    for (int32_t i = at; i >= 0 && n < 64; i = ws->q[i].parent) if (ws->q[i].slot != FP_NONE) chain[n++] = i;
    *f = *base;
    while (n--) fset(f, ws->q[chain[n]].slot, ws->q[chain[n]].val);
}

/* vr.form.ranked_cards over the candidates in mask. Returns how many (<= limit), refs in out. */
static int ranked(fp_sess_t *s, uint32_t mask, const fp_cand_t *cands, int ncands, int limit, fp_ref_t *out, int max) {
    fp_rank_ws_t *ws = s->ws;
    int nout = 0, order = 0;
    for (int ci = 0; ci < ncands; ci++) {
        if (!(mask >> ci & 1)) continue;
        const fp_cand_t *c = &cands[ci];
        wset_t heard;
        wset_stems(c->heard, &heard);
        memset(ws->seen, 0, sizeof ws->seen);
        int head = 0, tail = 0, steps = 0;
        ws->q[tail++] = (typeof(ws->q[0])){-1, c->card, FP_NONE, FP_NONE, c->asking, 0, 0, 0.0};
        while (head < tail && nout < limit * 4 && steps < 2000) {
            int at = head++;
            fp_card card = ws->q[at].card;
            fp_str asking = ws->q[at].asking;
            fp_filled_t f;
            rebuild(ws, at, &c->filled, &f);
            if (!seen_add(ws, state_hash(card, asking, &f))) continue;
            steps++;
            if (asking == FP_NONE) {
                bool dup = false;
                for (int k = 0; k < nout; k++) if (ws->out[k].cand == ci && ws->out[k].card == card) dup = true;
                if (!dup && nout < 512) {
                    double sc = ws->q[at].ev - 0.1 * ws->q[at].n + 0.5 * c->p;
                    ws->out[nout] = (typeof(ws->out[0])){(uint8_t)ci, card, sc, order++, life_card(s, c->tree, card)};
                    nout++;
                }
                continue;
            }
            fp_tile_t ts[MAXT];
            int nt = card >= 0 ? fp_tiles(s, c->tree, card, ts, MAXT) : 0;
            for (int i = 0; i < nt; i++) {
                if (tail >= (int)(sizeof ws->q / sizeof ws->q[0])) { s->rank_overflow++; break; }
                if (truthy(s, ts[i].value)) {
                    fp_filled_t f2 = f;
                    fset(&f2, asking, ts[i].value);
                    fp_str na;
                    fp_card nc = walk(s, c->tree, &f2, card, &na);
                    ws->q[tail++] = (typeof(ws->q[0])){at, nc, asking, ts[i].value, na, (uint8_t)(ws->q[at].n + 1), 0,
                        ws->q[at].ev + soft(s, c, &heard, asking, ts[i].value, ts[i].label)};
                } else if (ts[i].to_node >= 0) {
                    ws->q[tail++] = (typeof(ws->q[0])){at, ts[i].to_node, FP_NONE, FP_NONE, FP_NONE, ws->q[at].n, 1, ws->q[at].ev};
                }
            }
        }
    }
    /* sort: life cards first, then score desc, then discovery order */
    for (int i = 1; i < nout; i++)
        for (int j = i; j > 0; j--) {
            typeof(ws->out[0]) *a = &ws->out[j - 1], *b = &ws->out[j];
            bool swap = (a->life != b->life) ? (!a->life && b->life)
                      : (a->score != b->score) ? (b->score > a->score) : (b->order < a->order);
            if (!swap) break;
            typeof(ws->out[0]) tmp = *a; *a = *b; *b = tmp;
        }
    int n = nout < limit ? nout : limit;
    if (n > max) n = max;
    for (int i = 0; i < n; i++) out[i] = (fp_ref_t){cands[ws->out[i].cand].tree, ws->out[i].card};
    return n;
}

int fp_ranked_cards(fp_sess_t *s, int limit, fp_ref_t *out, int max) {
    return ranked(s, (1u << s->ncands) - 1, s->cands, s->ncands, limit, out, max);
}

/* vr.form.most_telling */
fp_str fp_most_telling(fp_sess_t *s) {
    fp_str best = FP_NONE;
    double best_h = -1.0;
    fp_ref_t tmp[FP_MAX_RANKED];
    for (int ci = 0; ci < s->ncands; ci++) {
        fp_cand_t *c = &s->cands[ci];
        if (c->asking == FP_NONE) continue;
        fp_tile_t ts[MAXT];
        int nt = fp_tiles(s, c->tree, c->card, ts, MAXT), counts[MAXT], nc = 0, tot = 0;
        for (int i = 0; i < nt; i++) {
            if (!truthy(s, ts[i].value)) continue;
            fp_cand_t sub = *c;
            fset(&sub.filled, c->asking, ts[i].value);
            advance(s, &sub, c->card);
            int n = ranked(s, 1, &sub, 1, 40, tmp, FP_MAX_RANKED);
            counts[nc++] = n > 1 ? n : 1;
        }
        for (int i = 0; i < nc; i++) tot += counts[i];
        double h = 0.0;
        if (tot) {
            double sum = 0.0;
            for (int i = 0; i < nc; i++) sum += (double)counts[i] / tot * log((double)counts[i] / tot);
            h = -sum;
        }
        if (h > best_h) { best = c->asking; best_h = h; }
    }
    return best;
}

/* vr.form._lookahead + open_fields */
int fp_open_fields(fp_sess_t *s, fp_field_t *out, int max) {
    int n = 0;
    if (s->ncands > 1 && n < max) {
        out[n++] = (fp_field_t){FP_TOPIC, FP_NONE, 0, 0, (1u << s->ncands) - 1, FP_NONE, FP_NONE};
    }
    fp_field_t rows[FP_MAX_FIELDS * 2]; int nr = 0;
    static struct { int card; fp_str asking; fp_filled_t f; fp_str ws, wv; } q[128];
    for (int ci = 0; ci < s->ncands; ci++) {
        fp_cand_t *c = &s->cands[ci];
        fp_str seen[8]; int nseen = 0, nout = 0, head = 0, tail = 0;
        if (c->card >= 0) { q[tail].card = c->card; q[tail].asking = c->asking; q[tail].f = c->filled; q[tail].ws = q[tail].wv = FP_NONE; tail++; }
        while (head < tail && nout < 6) {
            int at = head++;
            fp_str asking = q[at].asking;
            if (asking == FP_NONE) continue;
            bool dup = false;
            for (int k = 0; k < nseen; k++) if (seen[k] == asking) dup = true;
            if (dup) continue;
            seen[nseen++] = asking; nout++;
            int r = -1;                                 /* shared slot names appear once */
            for (int k = 0; k < nr; k++) if (rows[k].key == asking) r = k;
            if (r >= 0) rows[r].trees |= 1u << ci;
            else if (nr < (int)(sizeof rows / sizeof rows[0]))
                rows[nr++] = (fp_field_t){asking, N_LABEL(node(s, c->tree, q[at].card)), c->tree, (uint16_t)q[at].card,
                                          1u << ci, q[at].ws, q[at].wv};
            fp_tile_t ts[MAXT];
            int nt = fp_tiles(s, c->tree, q[at].card, ts, MAXT);
            for (int i = 0; i < nt && tail < 128; i++) {
                if (!truthy(s, ts[i].value)) continue;
                fp_filled_t f2 = q[at].f;
                fset(&f2, asking, ts[i].value);
                fp_str na;
                fp_card nc = walk(s, c->tree, &f2, q[at].card, &na);
                if (na != FP_NONE) {
                    q[tail].card = nc; q[tail].asking = na; q[tail].f = f2; q[tail].ws = asking; q[tail].wv = ts[i].value; tail++;
                }
            }
        }
    }
    fp_str top = fp_most_telling(s);
    /* stable sort by (key not in life, key != top) */
    int rank[FP_MAX_FIELDS * 2];
    for (int k = 0; k < nr; k++) {
        bool life = false;
        for (int ci = 0; ci < s->ncands && !life; ci++) life = life_slot(s, s->cands[ci].tree, rows[k].key, false);
        rank[k] = (life ? 0 : 2) + (rows[k].key == top ? 0 : 1);
    }
    for (int r = 0; r <= 3; r++)
        for (int k = 0; k < nr; k++) if (rank[k] == r && n < max) out[n++] = rows[k];
    return n;
}

/* vr.form._named: the tree's title words (minus filler) were all said */
static bool named(const fp_sess_t *s, const fp_cand_t *c, const fp_toks_t *words) {
    fp_toks_t t;
    fp_tokenize(S(s->trees[c->tree].title), &t);
    int k = 0;
    for (int i = 0; i < t.n; i++) {
        if (in(t.w[i], FILLER)) continue;
        k++;
        if (!tok_has(words, t.w[i])) return false;
    }
    return k > 0;
}

/* vr.form._step: an answer always moves the form on */
static void step(fp_sess_t *s, fp_cand_t *c, const fp_tile_t *tile) {
    fp_card was = c->card; fp_str wa = c->asking;
    advance(s, c, c->card);
    if (c->card == was && c->asking == wa && tile && truthy(s, tile->to)) {
        if (tile->to_node >= 0) advance(s, c, tile->to_node);
        else { c->card = FP_EXT(tile->to); c->asking = FP_NONE; }
    }
}

static fp_result_t done_or_form(fp_sess_t *s) {
    if (s->ncands == 1 && s->cands[0].asking == FP_NONE) return res(FP_OPEN, s->cands[0].tree, s->cands[0].card);
    return res(FP_FORM, 0, 0);
}

static void keep_only(fp_sess_t *s, int ci) { if (ci) s->cands[0] = s->cands[ci]; s->ncands = 1; }

/* vr.form.answer */
fp_result_t fp_answer(fp_sess_t *s, const char *text) {
    fp_toks_t words;
    fp_tokenize(text, &words);
    if (tok_has(&words, "skip") || tok_has(&words, "dunno") || (tok_has(&words, "don't") && tok_has(&words, "know")) ||
        (tok_has(&words, "no") && tok_has(&words, "time")))
        return res(FP_SKIP, 0, 0);
    if (s->ncands > 1) {                             /* fields[0] is the "which is it" field */
        int hit = -1, nh = 0;
        for (int i = 0; i < s->ncands; i++) if (named(s, &s->cands[i], &words)) { hit = i; nh++; }
        if (nh == 1) keep_only(s, hit);
    }
    struct { uint8_t tree; fp_card card; fp_str asking; } states[256];
    int nst = 0, passes = 0;
    bool progressed = true;
    while (progressed && passes < 20) {
        progressed = false; passes++;
        for (int i = 0; i < s->ncands; i++) {
            fp_cand_t *c = &s->cands[i];
            if (c->asking == FP_NONE) continue;
            bool was = false;
            for (int k = 0; k < nst; k++)
                if (states[k].tree == c->tree && states[k].card == c->card && states[k].asking == c->asking) was = true;
            if (was) continue;
            if (nst < 256) { states[nst].tree = c->tree; states[nst].card = c->card; states[nst].asking = c->asking; nst++; }
            fp_filled_t f = c->filled;
            int pick = c->card >= 0 ? answer_turn(s, c->tree, text, c->card, c->asking, &f) : -1;
            bool extra = false;                      /* other blanks heard in the same breath stay */
            for (int k = 0; k < f.n; k++)
                if (!fhas(&c->filled, f.slot[k]) && f.slot[k] != c->asking) { fset(&c->filled, f.slot[k], f.val[k]); extra = true; }
            if (extra) {                             /* ("he's unresponsive now" on any field): life first */
                int go = rule_hit(s, c);
                if (go >= 0) return res(FP_OPEN, c->tree, rule_card(s, c->tree, go));
            }
            fp_tile_t ts[MAXT];
            if (c->card >= 0) fp_tiles(s, c->tree, c->card, ts, MAXT);
            bool has_tile = pick >= 0;
            fp_str value = has_tile ? ts[pick].value : FP_NONE;
            if (!truthy(s, value)) {
                fp_str v = snap(s, text, c);         /* madlibs: a near word snaps to its answer */
                if (v != FP_NONE) { f = c->filled; fset(&f, c->asking, v); value = v; has_tile = false; }
            }
            if (truthy(s, value)) {
                /* {**filled, **c.filled, asking: filled.get(asking, value)}: never overwrite a filled blank */
                fp_str av = fget(&f, c->asking);
                if (av == FP_NONE) av = value;
                for (int k = 0; k < c->filled.n; k++) fset(&f, c->filled.slot[k], c->filled.val[k]);
                fset(&f, c->asking, av);
                c->filled = f;
                int go = rule_hit(s, c);
                if (go >= 0) return res(FP_OPEN, c->tree, rule_card(s, c->tree, go));
                step(s, c, has_tile && truthy(s, ts[pick].to) ? &ts[pick] : NULL);
                progressed = true;
            }
        }
    }
    return done_or_form(s);
}

/* vr.form.pick */
fp_result_t fp_pick(fp_sess_t *s, const char *key, const char *value, const char *to) {
    if (!value && to && *to) {
        char cur[160];
        snprintf(cur, sizeof cur, "%s", to);
        for (int i = 0; i < s->ncands; i++) {
            fp_cand_t *c = &s->cands[i];
            if (c->asking == FP_NONE || strcmp(S(c->asking), key)) continue;
            const char *nm = S(s->trees[c->tree].name);
            size_t L = strlen(nm);
            if (!strncmp(cur, nm, L) && cur[L] == '/') memmove(cur, cur + L + 1, strlen(cur + L + 1) + 1);
            int nd = fp_node_by_key(s, c->tree, cur);
            if (nd >= 0) advance(s, c, nd);
            else if (strchr(cur, '/')) {               /* a plain tile into another tree ("Start CPR") */
                int sid = fp_find_str(&s->pk, cur);
                if (sid >= 0) return res(FP_OPEN, c->tree, FP_EXT((fp_str)sid));
            }
        }
    } else if (!strcmp(key, "@topic")) {
        for (int i = 0; i < s->ncands; i++)
            if (value && !strcmp(S(s->trees[s->cands[i].tree].name), value)) { keep_only(s, i); break; }
    } else if (value) {
        int ks = fp_find_str(&s->pk, key), vs = fp_find_str(&s->pk, value);
        for (int i = 0; i < s->ncands && ks >= 0 && vs >= 0; i++) {
            fp_cand_t *c = &s->cands[i];
            if (slot_index(s, c->tree, (fp_str)ks) < 0 && c->asking != ks) continue;
            fset(&c->filled, (fp_str)ks, (fp_str)vs);
            if (c->asking != FP_NONE) {
                fp_tile_t ts[MAXT], *tile = NULL;
                int nt = c->card >= 0 ? fp_tiles(s, c->tree, c->card, ts, MAXT) : 0;
                for (int k = 0; k < nt; k++) if (ts[k].value == vs) { tile = &ts[k]; break; }
                step(s, c, tile);
            }
        }
    }
    return done_or_form(s);
}
