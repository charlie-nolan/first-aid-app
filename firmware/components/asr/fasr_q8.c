/* fasr_q8.c - see fasr_q8.h. Activations are channel-major (C x T) as in the reference. */
#include "fasr_q8.h"
#include <math.h>
#include <string.h>

static inline uint16_t r16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }
static inline int16_t rs16(const uint8_t *p) { return (int16_t)r16(p); }
static inline uint32_t r32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
static inline float rf(const uint8_t *p) { float f; uint32_t u = r32(p); memcpy(&f, &u, 4); return f; }

#define LAYER(m, i) ((m)->layers + 32 * (i))
#define L_CIN(l) r16(l)
#define L_COUT(l) r16((l) + 2)
#define L_K(l) r16((l) + 4)
#define L_STRIDE(l) r16((l) + 6)
#define L_PAD(l) r16((l) + 8)
#define L_GROUPS(l) r16((l) + 10)
#define L_EIN(l) ((int8_t)(l)[12])
#define L_EOUT(l) ((int8_t)(l)[13])
#define L_ERES(l) ((int8_t)(l)[14])
#define L_FLAGS(l) ((l)[15])
#define L_W(m, l) ((const int8_t *)((m)->w + r32((l) + 16)))
#define L_B(m, l) ((m)->w + r32((l) + 20))
#define L_EW(m, l) ((const int8_t *)((m)->w + r32((l) + 24)))

bool fq_open(fq_model_t *m, const void *blob, size_t len) {
    const uint8_t *b = blob;
    if (len < 16 + 320 || memcmp(b, "FQ81", 4) || r16(b + 4) != 1) return false;
    m->b = b; m->nl = r16(b + 6); m->e_in = rs16(b + 8);
    m->mu = b + 16; m->sd = b + 16 + 160; m->layers = b + 16 + 320;
    uint32_t off = r32(b + 12);
    if (off > len || 16 + 320 + 32u * m->nl > off) return false;
    m->w = b + off;
    m->cmax = 40; m->ctok = 0;
    for (int i = 0; i < m->nl; i++) {
        const uint8_t *l = LAYER(m, i);
        if (L_COUT(l) > m->cmax) m->cmax = L_COUT(l);
        if (L_FLAGS(l) & 4) m->ctok = L_COUT(l);
    }
    return m->ctok > 0;
}

int fq_out_len(const fq_model_t *m, int T) {
    const uint8_t *l = LAYER(m, 0);
    return (T + L_PAD(l) - L_K(l)) / L_STRIDE(l) + 1;
}

size_t fq_ws_size(const fq_model_t *m, int T) {
    return 3 * (size_t)m->cmax * (T > 0 ? T : 1);   /* three int8 C x T planes (x, y, block input) */
}

static inline int64_t rshift_round(int64_t v, int s) {
    if (s > 0) return (v + ((int64_t)1 << (s - 1))) >> s;
    if (s < 0) return v << -s;
    return v;
}

/* one conv layer: x (cin x T) -> acc per output, requantised into y (cout x To), or int32 for the head.
 * Integer sums, so the loop order (per weight, then a run of t) gives the same result as the reference;
 * this order reads x in sequence and skips the padding test in the inner loop. */
#define FQ_TMAX 2048
static int32_t fq_acc[FQ_TMAX];
static int conv(const fq_model_t *m, const uint8_t *l, const int8_t *x, int T, int8_t *y, const int8_t *blk, int32_t *head) {
    int cin = L_CIN(l), cout = L_COUT(l), k = L_K(l), s = L_STRIDE(l), pad = L_PAD(l), g = L_GROUPS(l);
    int To = (T + pad - k) / s + 1, fl = L_FLAGS(l);
    if (To > FQ_TMAX) return -1;
    const int8_t *w = L_W(m, l), *ew = L_EW(m, l);
    const uint8_t *bb = L_B(m, l);
    int cpg = cin / g;                                   /* input channels per group (1 for depthwise) */
    int lo = (fl & 1) || (fl & 2) ? 0 : -128;
    int32_t *acc = fq_acc;
    for (int o = 0; o < cout; o++) {
        int32_t bias = (int32_t)r32(bb + 4 * o);
        const int8_t *wo = w + (size_t)o * cpg * k;
        int ib = g == 1 ? 0 : o * cpg;
        int sh = L_EIN(l) + ew[o] - L_EOUT(l);
        for (int t = 0; t < To; t++) acc[t] = bias;
        for (int i = 0; i < cpg; i++) {
            const int8_t *xi = x + (size_t)(ib + i) * T, *wi = wo + i * k;
            for (int kk = 0; kk < k; kk++) {
                int32_t wv = wi[kk];
                if (!wv) continue;
                int off = kk - pad;                      /* input index = t * s + off */
                int ta = off >= 0 ? 0 : (-off + s - 1) / s;
                int tb = (T - 1 - off) < 0 ? -1 : (T - 1 - off) / s;
                if (tb > To - 1) tb = To - 1;
                if (s == 1) { const int8_t *xp = xi + off; for (int t = ta; t <= tb; t++) acc[t] += (int32_t)xp[t] * wv; }
                else for (int t = ta; t <= tb; t++) acc[t] += (int32_t)xi[t * s + off] * wv;
            }
        }
        for (int t = 0; t < To; t++) {
            if (fl & 4) { head[(size_t)o * To + t] = acc[t]; continue; }
            int64_t v = rshift_round(acc[t], sh);
            if (fl & 2) v += rshift_round(blk[(size_t)o * To + t], L_ERES(l) - L_EOUT(l));
            if (v < lo) v = lo;
            if (v > 127) v = 127;
            y[(size_t)o * To + t] = (int8_t)v;
        }
    }
    return To;
}

int fq_forward(const fq_model_t *m, const float *feats, int T, void *ws, int32_t *logits, fq_layer_cb cb, void *ctx) {
    size_t plane = (size_t)m->cmax * (T > 0 ? T : 1);
    int8_t *x = ws, *y = x + plane, *blk = y + plane;
    double sc = ldexp(1.0, m->e_in);
    for (int c = 0; c < 40; c++) {                       /* (feats - mu) / sd, quantised; channel-major */
        double mu = rf(m->mu + 4 * c), sd = rf(m->sd + 4 * c);
        for (int t = 0; t < T; t++) {
            double v = nearbyint(((double)feats[(size_t)t * 40 + c] - mu) / sd * sc);
            x[(size_t)c * T + t] = (int8_t)(v < -128 ? -128 : v > 127 ? 127 : v);
        }
    }
    int Tc = T;
    for (int i = 0; i < m->nl; i++) {
        const uint8_t *l = LAYER(m, i);
        int is_dw = L_GROUPS(l) > 1;
        if (is_dw) memcpy(blk, x, (size_t)L_CIN(l) * Tc);  /* block input, for the residual */
        int To = conv(m, l, x, Tc, y, blk, logits);
        if (To < 0) return -1;
        if (L_FLAGS(l) & 4) return To;
        if (cb) cb(ctx, i, y, L_COUT(l), To);
        int8_t *t = x; x = y; y = t;
        Tc = To;
    }
    return -1;
}

int fq_logit_exp(const fq_model_t *m, int c) {
    for (int i = 0; i < m->nl; i++) {
        const uint8_t *l = LAYER(m, i);
        if (L_FLAGS(l) & 4) return L_EIN(l) + L_EW(m, l)[c];
    }
    return 0;
}

void fq_logp(const fq_model_t *m, const int32_t *logits, int T, float *logp) {
    int C = m->ctok;
    double z[512], sc[512];
    for (int c = 0; c < C && c < 512; c++) sc[c] = ldexp(1.0, -fq_logit_exp(m, c));   /* same value, once */
    for (int t = 0; t < T; t++) {
        double mx = -1e300;
        for (int c = 0; c < C && c < 512; c++) {
            z[c] = (double)logits[(size_t)c * T + t] * sc[c];
            if (z[c] > mx) mx = z[c];
        }
        double s = 0;
        for (int c = 0; c < C; c++) { z[c] -= mx; s += exp(z[c]); }
        double ls = log(s);
        for (int c = 0; c < C; c++) logp[(size_t)t * C + c] = (float)(z[c] - ls);
    }
}
