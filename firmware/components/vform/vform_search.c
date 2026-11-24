/* vform_search.c - see vform_search.h. Ranking (vr.search.search), in order: life cards first (when
 * the query names their tree whole, or score >= 2 and 3 * score >= the best score), then nodes of a
 * tree the query names whole, then score (every query word must match), then root tiles, depth,
 * and the (tree, node id) order of the Python sort. */
#include "vform_search.h"
#include "vform.h"
#include <stdio.h>
#include <string.h>

static inline uint16_t r16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }
static inline uint32_t r32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
#define STR(x, o) ((const char *)(x)->b + (o))

bool fs_open(fs_index_t *x, const void *blob, size_t len) {
    const uint8_t *b = blob;
    if (!b || len < 48 || memcmp(b, "SR01", 4) || r16(b + 4) != 1) return false;
    x->b = b; x->len = len; x->ntrees = r16(b + 6); x->nkeys = r32(b + 8); x->nnodes = r32(b + 12);
    return true;
}

static int key_id(const fs_index_t *x, const char *k) {
    const uint8_t *t = x->b + r32(x->b + 16);
    uint32_t lo = 0, hi = x->nkeys;
    while (lo < hi) {
        uint32_t m = (lo + hi) / 2;
        int c = strcmp(STR(x, r32(t + 4 * m)), k);
        if (!c) return (int)m;
        if (c < 0) lo = m + 1; else hi = m;
    }
    return -1;
}
static bool is_stop(const fs_index_t *x, const char *w) {
    const uint8_t *t = x->b + r32(x->b + 36);
    for (uint32_t i = 0; i < r32(x->b + 40); i++) if (!strcmp(STR(x, r32(t + 4 * i)), w)) return true;
    return false;
}
static void key_of(const char *w, char *out) {       /* vr.form._key: stem; drop a final e from 5+ letters */
    fp_stem(w, true, out);
    size_t L = strlen(out);
    if (L >= 5 && out[L - 1] == 'e') out[L - 1] = 0;
}
static uint32_t weight(const fs_index_t *x, const uint8_t *nd, int k) {
    const uint8_t *w = x->b + r32(nd + 20);
    for (int i = 0; i < r16(nd + 6); i++) if ((int)r32(w + 8 * i) == k) return r32(w + 8 * i + 4);
    return 0;
}
static bool in_ids(const uint8_t *ids, uint32_t n, int k) {
    for (uint32_t i = 0; i < n; i++) if ((int)r32(ids + 4 * i) == k) return true;
    return false;
}

typedef struct { uint32_t node; int score; bool life_first, named; } cand_t;

int fs_search(const fs_index_t *x, const char *query, fs_hit_t *out) {
    fp_toks_t t;
    fp_tokenize(query, &t);
    char qs[FP_MAX_TOK][FP_WLEN]; int nq = 0;        /* sorted unique keys, as sorted(set) */
    for (int i = 0; i < t.n; i++) {
        if (is_stop(x, t.w[i])) continue;
        char k[FP_WLEN]; key_of(t.w[i], k);
        int at = nq;
        for (int j = 0; j < nq; j++) { int c = strcmp(qs[j], k); if (!c) { at = -1; break; } if (c > 0) { at = j; break; } }
        if (at < 0) continue;
        memmove(qs[at + 1], qs[at], (size_t)(nq - at) * FP_WLEN);
        strcpy(qs[at], k); nq++;
    }
    if (!nq) return 0;
    int qk[FP_MAX_TOK];
    for (int i = 0; i < nq; i++) qk[i] = key_id(x, qs[i]);
    const uint8_t *nodes = x->b + r32(x->b + 24), *trees = x->b + r32(x->b + 20), *al = x->b + r32(x->b + 28);
    static cand_t c[1024]; int nc = 0, top = 0;
    for (uint32_t n = 0; n < x->nnodes && nc < 1024; n++) {
        const uint8_t *nd = nodes + 24 * n, *tr = trees + 16 * r16(nd);
        int score = 0; bool ok = true;
        for (int i = 0; i < nq && ok; i++) {
            uint32_t w = qk[i] >= 0 ? weight(x, nd, qk[i]) : 0;
            for (uint32_t a = 0; a < r32(x->b + 32); a++) {            /* aliases: "blood" also searches "bleed" */
                const uint8_t *ar = al + 12 * a;
                if ((int)r32(ar) != qk[i]) continue;
                for (uint32_t j = 0; j < r32(ar + 4); j++) { uint32_t w2 = weight(x, nd, (int)r32(x->b + r32(ar + 8) + 4 * j)); if (w2 > w) w = w2; }
            }
            if (!w && qk[i] >= 0 && in_ids(x->b + r32(tr + 4), r32(tr), qk[i])) w = 1;   /* names the topic: browse it */
            if (!w) ok = false;
            score += (int)w;
        }
        if (!ok) continue;
        bool named = false;                                            /* a whole title/condition name in the query */
        for (uint32_t m = 0; m < r32(tr + 8) && !named; m++) {
            const uint8_t *nm = x->b + r32(tr + 12) + 8 * m;
            bool all = r32(nm) > 0;
            for (uint32_t j = 0; j < r32(nm) && all; j++) {
                int k = (int)r32(x->b + r32(nm + 4) + 4 * j), hit = 0;
                for (int i = 0; i < nq; i++) if (qk[i] == k) hit = 1;
                if (!hit) all = false;
            }
            named = all;
        }
        c[nc].node = n; c[nc].score = score; c[nc].named = named; nc++;
        if (score > top) top = score;
    }
    for (int i = 0; i < nc; i++) {
        const uint8_t *nd = nodes + 24 * c[i].node;
        c[i].life_first = nd[2] && (c[i].named || (c[i].score >= 2 && 3 * c[i].score >= top));
    }
    /* insertion sort by the Python key (not life_first, not named, -score, not root, depth, order) */
    for (int i = 1; i < nc; i++) {
        cand_t v = c[i]; int j = i;
        while (j > 0) {
            cand_t *u = &c[j - 1];
            const uint8_t *a = nodes + 24 * u->node, *b = nodes + 24 * v.node;
            int d = (int)!v.life_first - (int)!u->life_first;
            if (!d) d = (int)!v.named - (int)!u->named;
            if (!d) d = u->score - v.score;
            if (!d) d = (int)!b[3] - (int)!a[3];
            if (!d) d = (int)r16(b + 4) - (int)r16(a + 4);
            if (!d) d = r32(b + 8) < r32(a + 8) ? -1 : 1;
            if (d >= 0) break;
            c[j] = c[j - 1]; j--;
        }
        c[j] = v;
    }
    int n = nc < FS_MAX_HITS ? nc : FS_MAX_HITS;
    for (int i = 0; i < n; i++) {
        const uint8_t *nd = nodes + 24 * c[i].node;
        out[i].node = STR(x, r32(nd + 12)); out[i].title = STR(x, r32(nd + 16));
        out[i].life = nd[2]; out[i].score = c[i].score;
    }
    return n;
}

/* JSON string with ensure_ascii, cut to maxcp code points (as vform_show.c) */
static size_t js(char *o, size_t cap, const char *s, int maxcp) {
    size_t n = 0;
#define PUT(ch) do { if (n + 1 < cap) o[n] = (ch); n++; } while (0)
    PUT('"');
    for (const unsigned char *p = (const unsigned char *)s; *p && maxcp; maxcp--) {
        unsigned c = *p; int L = 1;
        if (c >= 0x80) {
            int k = (c & 0xE0) == 0xC0 ? 2 : (c & 0xF0) == 0xE0 ? 3 : (c & 0xF8) == 0xF0 ? 4 : 1;
            unsigned v = k > 1 ? c & (0x7F >> k) : 0xFFFD;
            for (int i = 1; i < k; i++) v = v << 6 | (p[i] & 0x3F);
            c = v; L = k;
        }
        p += L;
        char e[16];
        if (c == '"' || c == '\\') { PUT('\\'); PUT((char)c); }
        else if (c == '\n') { PUT('\\'); PUT('n'); }
        else if (c < 0x20 || c >= 0x7F) {
            if (c >= 0x10000) { c -= 0x10000; snprintf(e, sizeof e, "\\u%04x\\u%04x", 0xD800 + (c >> 10), 0xDC00 + (c & 0x3FF)); }
            else snprintf(e, sizeof e, "\\u%04x", c);
            for (char *q = e; *q; q++) PUT(*q);
        } else PUT((char)c);
    }
    PUT('"');
    if (cap) o[n < cap ? n : cap - 1] = 0;
#undef PUT
    return n;
}

int fs_frame_json(const fs_index_t *x, const char *query, char *out, size_t cap) {
    static fs_hit_t h[FS_MAX_HITS];
    int n = fs_search(x, query, h);
    size_t o = 0;
#define APP(...) do { int k = snprintf(out + (o < cap ? o : cap), o < cap ? cap - o : 0, __VA_ARGS__); o += k > 0 ? k : 0; } while (0)
    APP("{\"v\":1,\"kind\":\"search\",\"query\":");
    o += js(out + (o < cap ? o : cap), o < cap ? cap - o : 0, query, 100);
    APP(",\"hits\":%d,\"pages\":[", n);
    for (int i = 0; i < n; i++) {
        if (i % FS_PAGE == 0) APP("%s[", i ? "]," : "");
        else APP(",");
        APP("{\"title\":");
        o += js(out + (o < cap ? o : cap), o < cap ? cap - o : 0, h[i].title, 78);
        APP(",\"node\":");
        o += js(out + (o < cap ? o : cap), o < cap ? cap - o : 0, h[i].node, 78);
        APP(",\"life\":%s}", h[i].life ? "true" : "false");
    }
    APP("%s]}", n ? "]" : "");
#undef APP
    return (int)o;
}
