#include "vrouter.h"
#include <string.h>
#include <math.h>

#define HASH_BIGRAM 1
#define HASH_NEG 2
#define NEG_WINDOW 3

static uint32_t fnv1a(uint8_t kind, uint16_t a, uint16_t b, uint32_t buckets) {
    const uint8_t by[5] = { kind, (uint8_t)a, (uint8_t)(a >> 8), (uint8_t)b, (uint8_t)(b >> 8) };
    uint32_t h = 0x811C9DC5u;
    for (int i = 0; i < 5; i++) { h ^= by[i]; h *= 0x01000193u; }
    return h % buckets;
}


static const uint8_t *take(const uint8_t **p, size_t n) { const uint8_t *r = *p; *p += n; return r; }

bool vr_open(vr_model_t *m, const void *blob, size_t len) {
    const uint8_t *p = blob, *end = p + len;
    if (len < 24 || memcmp(p, "VXP1", 4)) return false;
    uint16_t f[10];
    memcpy(f, p + 4, sizeof f);
    if (f[0] != 1) return false;
    m->kind = f[1]; m->V = f[2]; m->B = f[3]; m->C = f[4]; m->d = f[5]; m->hidden = f[6]; m->k = f[7];
    if (m->d > VR_MAX_D || m->hidden > VR_MAX_HIDDEN || m->k > VR_MAX_CLASSES || !m->B) return false;
    p += 24;
    m->cls = take(&p, (m->V + 3u) & ~3u);
    m->ws = (const float *)take(&p, 4u * m->V); m->w = (const int8_t *)take(&p, (size_t)m->V * m->d);
    m->hs = (const float *)take(&p, 4u * m->B); m->h = (const int8_t *)take(&p, (size_t)m->B * m->d);
    m->cs = (const float *)take(&p, 4u * m->C); m->c = (const int8_t *)take(&p, (size_t)m->C * m->d);
    m->s1 = (const float *)take(&p, 4u * m->hidden); m->b1 = (const float *)take(&p, 4u * m->hidden);
    m->w1 = (const int8_t *)take(&p, (size_t)m->hidden * m->d);
    m->s2 = (const float *)take(&p, 4u * m->k); m->b2 = (const float *)take(&p, 4u * m->k);
    m->w2 = (const int8_t *)take(&p, (size_t)m->k * m->hidden);
    m->class_map = (const uint16_t *)take(&p, 2u * m->k);
    m->names = (const char *)p;
    p += f[8];
    return p <= end;
}

static void add_row(float *x, const int8_t *row, float s, int d) {
    for (int i = 0; i < d; i++) x[i] += row[i] * s;
}

void vr_score(const vr_model_t *m, const voice_hyp_t *hyp, int ctx, float *p) {
    float xw[VR_MAX_D] = {0}, xh[VR_MAX_D] = {0}, x[VR_MAX_D], h[VR_MAX_HIDDEN];
    float sw = 0, sh = 0;
    const int d = m->d;
    float rank_w = 1.0f;
    for (int r = 0; r < hyp->n && r < VR_MAX_HYP; r++, rank_w *= 0.6f) {
        int len = hyp->h[r].len;
        if (!len) continue;
        float mean = 0;
        for (int j = 0; j < len; j++) mean += hyp->h[r].conf[j];
        float hw = rank_w * (mean / len / 100.0f);
        uint16_t prev = 0; int neg_left = 0;
        for (int j = 0; j < len; j++) {
            uint16_t id = hyp->h[r].id[j];
            float w = hw * hyp->h[r].conf[j] / 100.0f;
            bool neg = id && id < m->V && m->cls[id] == VR_CLS_NEG;
            if (id && id < m->V && w > 0) {
                add_row(xw, m->w + (size_t)id * d, m->ws[id] * w, d); sw += w;
                if (prev) { uint32_t b = fnv1a(HASH_BIGRAM, prev, id, m->B); add_row(xh, m->h + (size_t)b * d, m->hs[b] * w, d); sh += w; }
                if (neg_left && !neg) { uint32_t b = fnv1a(HASH_NEG, 0, id, m->B); add_row(xh, m->h + (size_t)b * d, m->hs[b] * w, d); sh += w; }
            }
            if (neg) neg_left = NEG_WINDOW; else if (neg_left) neg_left--;
            prev = id;
        }
    }
    for (int i = 0; i < d; i++) x[i] = (sw > 0 ? xw[i] / sw : 0) + (sh > 0 ? xh[i] / sh : 0);
    if (ctx >= 0 && ctx < m->C) add_row(x, m->c + (size_t)ctx * d, m->cs[ctx], d);
    for (int o = 0; o < m->hidden; o++) {
        const int8_t *row = m->w1 + (size_t)o * d; float a = 0;
        for (int i = 0; i < d; i++) a += row[i] * x[i];
        a = a * m->s1[o] + m->b1[o];
        h[o] = a > 0 ? a : 0;
    }
    float mx = -1e30f;
    for (int o = 0; o < m->k; o++) {
        const int8_t *row = m->w2 + (size_t)o * m->hidden; float a = 0;
        for (int i = 0; i < m->hidden; i++) a += row[i] * h[i];
        p[o] = a * m->s2[o] + m->b2[o];
        if (p[o] > mx) mx = p[o];
    }
    float sum = 0;
    for (int o = 0; o < m->k; o++) { p[o] = expf(p[o] - mx); sum += p[o]; }
    for (int o = 0; o < m->k; o++) p[o] /= sum;
}

void vr_top3(const vr_model_t *m, const float *p, int out[3]) {
    out[0] = out[1] = out[2] = -1;
    for (int o = 0; o < m->k; o++)
        for (int t = 0; t < 3; t++)
            if (out[t] < 0 || p[o] > p[out[t]]) {
                for (int u = 2; u > t; u--) out[u] = out[u - 1];
                out[t] = o; break;
            }
}

const char *vr_name(const vr_model_t *m, int cls) {
    const char *s = m->names;
    for (int i = 0; i < cls; i++) s += strlen(s) + 1;
    return s;
}
