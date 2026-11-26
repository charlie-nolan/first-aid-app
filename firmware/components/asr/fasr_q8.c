/* fasr_q8.c - see fasr_q8.h. Activations are channel-major (C x T) as in the reference. */
#include "fasr_q8.h"
#include <math.h>
#include <string.h>
#include <stdbool.h>
#ifdef ESP_PLATFORM
#include "sdkconfig.h"
#include "esp_timer.h"
#include "esp_attr.h"
#define FP_BIG EXT_RAM_BSS_ATTR       /* big scratch arrays: in PSRAM on the chip (internal RAM is short) */
#define PROF_NOW() esp_timer_get_time()
#else
#define FP_BIG
#define PROF_NOW() 0
#endif
uint32_t fq_prof_us[5];
static fq_prefetch_t pf; static bool pf_on;
void fq_set_prefetch(const fq_prefetch_t *p) { pf_on = p != NULL; if (p) pf = *p; }
static const int8_t *pw_w;                  /* the current pointwise layer's weights in a prefetch buffer, or NULL */   /* input quantise, pointwise, depthwise, other convs, log-softmax (summed) */

/* Exact int8 dot product of n16 blocks of 16 bytes, both 16-byte aligned: the S3 vector unit on the chip
 * (fasr_dot_s3.S), plain C elsewhere. Integer sums, so both give the same value. */
#if defined(ESP_PLATFORM) && CONFIG_IDF_TARGET_ESP32S3
int32_t fq_dot16(const int8_t *a, const int8_t *b, int n16);
int32_t fq_dot16u(const int8_t *a, const int8_t *b, int n16);   /* b any alignment */
#else
#define fq_dot16u fq_dot16
static int32_t fq_dot16(const int8_t *a, const int8_t *b, int n16) {
    int32_t s = 0;
    for (int i = 0; i < 16 * n16; i++) s += (int32_t)a[i] * b[i];
    return s;
}
#endif

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
    if (m->ctok <= 0 || m->ctok > FQ_MAXTOK) return false;
    for (int c = 0; c < m->ctok; c++) m->lsc[c] = ldexp(1.0, -fq_logit_exp(m, c));   /* head scale per token, once */
    return true;
}

int fq_out_len(const fq_model_t *m, int T) {
    const uint8_t *l = LAYER(m, 0);
    return (T + L_PAD(l) - L_K(l)) / L_STRIDE(l) + 1;
}

size_t fq_ws_size(const fq_model_t *m, int T) {
    /* three int8 C x T planes (x, y, block input) + one T x C plane (pointwise input, 16-byte aligned) */
    return 4 * (size_t)m->cmax * (T > 0 ? T : 1) + 16;
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

static inline void fq_out(const uint8_t *l, int fl, int lo, int sh, int32_t acc, int o, int t, int To,
                          int8_t *y, const int8_t *blk, int32_t *head) {
    if (fl & 4) { head[(size_t)o * To + t] = acc; return; }
    int64_t v;
    if (sh > 0 && sh < 31 && acc < (1 << 29) && acc > -(1 << 29))   /* no overflow possible: same value in 32 bits */
        v = (acc + (1 << (sh - 1))) >> sh;
    else v = rshift_round(acc, sh);
    if (fl & 2) v += rshift_round(blk[(size_t)o * To + t], L_ERES(l) - L_EOUT(l));
    if (v < lo) v = lo;
    if (v > 127) v = 127;
    y[(size_t)o * To + t] = (int8_t)v;
}

/* pointwise (k 1, stride 1, no padding, cin a multiple of 16): x goes time-major into xt, then each
 * output is one vector dot product of a weight row with a frame. ~95 % of the model's work. */
static int conv_pw(const fq_model_t *m, const uint8_t *l, const int8_t *x, int T, int8_t *y, const int8_t *blk,
                   int32_t *head, int8_t *xt) {
    int cin = L_CIN(l), cout = L_COUT(l), fl = L_FLAGS(l), n16 = cin / 16;
    const int8_t *w = pw_w ? pw_w : L_W(m, l), *ew = L_EW(m, l);
    const uint8_t *bb = L_B(m, l);
    int lo = (fl & 1) || (fl & 2) ? 0 : -128;
    for (int i = 0; i < cin; i++) {
        const int8_t *xi = x + (size_t)i * T;
        for (int t = 0; t < T; t++) xt[(size_t)t * cin + i] = xi[t];
    }
    static int8_t wrow[1024] __attribute__((aligned(16)));
    int sr = (fl & 2) ? L_ERES(l) - L_EOUT(l) : 0;
    for (int o = 0; o < cout; o++) {
        const int8_t *wo = w + (size_t)o * cin;
        if (((uintptr_t)wo & 15) || cin > (int)sizeof wrow) { if (cin > (int)sizeof wrow) return -1; memcpy(wrow, wo, cin); wo = wrow; }
        int32_t bias = (int32_t)r32(bb + 4 * o);
        int sh = L_EIN(l) + ew[o] - L_EOUT(l);
        const int8_t *xp = xt;
        if (fl & 4) {
            int32_t *hp = head + (size_t)o * T;
            for (int t = 0; t < T; t++, xp += cin) hp[t] = bias + fq_dot16(xp, wo, n16);
            continue;
        }
        int8_t *yp = y + (size_t)o * T;
        const int8_t *bp = blk + (size_t)o * T;
        bool fast = sh > 0 && sh < 31;
        int32_t half = fast ? 1 << (sh - 1) : 0;
        for (int t = 0; t < T; t++, xp += cin) {
            int32_t acc = bias + fq_dot16(xp, wo, n16);
            int64_t v = fast && acc < (1 << 29) && acc > -(1 << 29) ? (int64_t)((acc + half) >> sh) : rshift_round(acc, sh);
            if (fl & 2) v += rshift_round(bp[t], sr);
            if (v < lo) v = lo;
            if (v > 127) v = 127;
            yp[t] = (int8_t)v;
        }
    }
    return T;
}
static FP_BIG int32_t fq_acc[FQ_TMAX];
/* depthwise, causal (left padding k - 1, stride 1, k <= 16): each output is one 16-wide dot product of the
 * channel's taps (zero-padded to 16, aligned copy) with 16 consecutive inputs; the first k - 1 outputs touch
 * the padding and go the plain way. Exact (integer sums). */
static int conv_dw(const fq_model_t *m, const uint8_t *l, const int8_t *x, int T, int t0, int8_t *y, const int8_t *blk, int32_t *head) {
    int cout = L_COUT(l), k = L_K(l), pad = L_PAD(l), fl = L_FLAGS(l);
    const int8_t *w = L_W(m, l), *ew = L_EW(m, l);
    const uint8_t *bb = L_B(m, l);
    int lo = (fl & 1) || (fl & 2) ? 0 : -128;
    static int8_t wk[16] __attribute__((aligned(16)));
    for (int o = 0; o < cout; o++) {
        memset(wk, 0, 16); memcpy(wk, w + (size_t)o * k, k);
        const int8_t *xo = x + (size_t)o * T;
        int32_t bias = (int32_t)r32(bb + 4 * o);
        int sh = L_EIN(l) + ew[o] - L_EOUT(l);
        for (int t = t0; t < T; t++) {                    /* outputs t0.. (streaming: the first t0 inputs are history) */
            int32_t acc = bias;
            if (t < pad) { for (int kk = pad - t; kk < k; kk++) acc += (int32_t)xo[t - pad + kk] * wk[kk]; }
            else acc += fq_dot16u(wk, xo + t - pad, 1);
            fq_out(l, fl, lo, sh, acc, o, t - t0, T - t0, y, blk, head);
        }
    }
    return T - t0;
}

/* any other conv (the stem: 40 x 5, stride 2) as im2col: each output frame's cin x k inputs in one aligned row
 * (zero padded to 16), weights copied to an aligned row, one dot per output. Exact. */
/* outputs [t0, t1) of a strided conv whose input rows have stride `stride` and Tin valid frames (zeros past them) */
static int conv_col_range(const fq_model_t *m, const uint8_t *l, const int8_t *x, int stride, int Tin, int t0, int t1,
                          int8_t *y, const int8_t *blk, int32_t *head, int8_t *col) {
    int cin = L_CIN(l), cout = L_COUT(l), k = L_K(l), s = L_STRIDE(l), pad = L_PAD(l), fl = L_FLAGS(l);
    int n = cin * k, n16 = (n + 15) / 16, rl = 16 * n16, No = t1 - t0;
    const int8_t *w = L_W(m, l), *ew = L_EW(m, l);
    const uint8_t *bb = L_B(m, l);
    int lo = (fl & 1) || (fl & 2) ? 0 : -128;
    static int8_t wr[1024] __attribute__((aligned(16)));
    if (rl > (int)sizeof wr) return -2;
    for (int t = t0; t < t1; t++) {
        int8_t *c = col + (size_t)(t - t0) * rl;
        for (int i = 0; i < cin; i++)
            for (int kk = 0; kk < k; kk++) { int ti = t * s - pad + kk; c[i * k + kk] = ti >= 0 && ti < Tin ? x[(size_t)i * stride + ti] : 0; }
        memset(c + n, 0, rl - n);
    }
    for (int o = 0; o < cout; o++) {
        memcpy(wr, w + (size_t)o * n, n); memset(wr + n, 0, rl - n);
        int32_t bias = (int32_t)r32(bb + 4 * o);
        int sh = L_EIN(l) + ew[o] - L_EOUT(l);
        for (int t = 0; t < No; t++) fq_out(l, fl, lo, sh, bias + fq_dot16(col + (size_t)t * rl, wr, n16), o, t, No, y, blk, head);
    }
    return No;
}
static int conv_col(const fq_model_t *m, const uint8_t *l, const int8_t *x, int T, int8_t *y, const int8_t *blk, int32_t *head,
                    int8_t *col) {
    int To = (T + L_PAD(l) - L_K(l)) / L_STRIDE(l) + 1;
    return conv_col_range(m, l, x, T, T, 0, To, y, blk, head, col);
}

static int conv(const fq_model_t *m, const uint8_t *l, const int8_t *x, int T, int8_t *y, const int8_t *blk, int32_t *head,
                int8_t *xt) {
    int cin = L_CIN(l), cout = L_COUT(l), k = L_K(l), s = L_STRIDE(l), pad = L_PAD(l), g = L_GROUPS(l);
    if (k == 1 && s == 1 && pad == 0 && g == 1 && cin % 16 == 0) {
        int64_t p0 = PROF_NOW(); int r = conv_pw(m, l, x, T, y, blk, head, xt);
        fq_prof_us[1] += (uint32_t)(PROF_NOW() - p0); return r;
    }
    int64_t p0 = PROF_NOW();
    int To = (T + pad - k) / s + 1, fl = L_FLAGS(l);
    if (To > FQ_TMAX) return -1;
    if (g == cin && g == cout && s == 1 && pad == k - 1 && k <= 16) {
        int r = conv_dw(m, l, x, T, 0, y, blk, head); fq_prof_us[2] += (uint32_t)(PROF_NOW() - p0); return r;
    }
    if (g == 1 && cin * k <= 1024 && (size_t)((cin * k + 15) / 16 * 16) * To <= (size_t)m->cmax * T) {
        int r = conv_col(m, l, x, T, y, blk, head, xt); fq_prof_us[3] += (uint32_t)(PROF_NOW() - p0); return r;
    }
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
        for (int t = 0; t < To; t++) fq_out(l, fl, lo, sh, acc[t], o, t, To, y, blk, head);
    }
    fq_prof_us[g > 1 ? 2 : 3] += (uint32_t)(PROF_NOW() - p0);
    return To;
}

/* (feats - mu) / sd, quantised, frames [t0, t1) into channel-major rows of stride `stride` */
static void quant_in(const fq_model_t *m, const float *feats, int t0, int t1, int8_t *x, int stride) {
    double sc = ldexp(1.0, m->e_in);
    for (int c = 0; c < 40; c++) {
        double mu = rf(m->mu + 4 * c), sd = rf(m->sd + 4 * c);
        for (int t = t0; t < t1; t++) {
            double v = nearbyint(((double)feats[(size_t)t * 40 + c] - mu) / sd * sc);
            x[(size_t)c * stride + t] = (int8_t)(v < -128 ? -128 : v > 127 ? 127 : v);
        }
    }
}
static bool is_pw(const uint8_t *l) { return L_K(l) == 1 && L_STRIDE(l) == 1 && L_PAD(l) == 0 && L_GROUPS(l) == 1 && L_CIN(l) % 16 == 0; }
static size_t pw_bytes(const uint8_t *l) { return (size_t)L_CIN(l) * L_COUT(l); }

static int run_layers(const fq_model_t *m, int8_t *x, int8_t *y, int8_t *blk, int8_t *xt, int Tc, int32_t *logits,
                      fq_layer_cb cb, void *ctx);
int fq_forward(const fq_model_t *m, const float *feats, int T, void *ws, int32_t *logits, fq_layer_cb cb, void *ctx) {
    size_t plane = (size_t)m->cmax * (T > 0 ? T : 1);
    int8_t *x = ws, *y = x + plane, *blk = y + plane;
    int8_t *xt = (int8_t *)(((uintptr_t)(blk + plane) + 15) & ~(uintptr_t)15);
    int64_t p0 = PROF_NOW();
    quant_in(m, feats, 0, T, x, T);
    fq_prof_us[0] += (uint32_t)(PROF_NOW() - p0);
    return run_layers(m, x, y, blk, xt, T, logits, cb, ctx);
}
int fq_forward_h(const fq_model_t *m, const int8_t *h, int C, int T, void *ws, int32_t *logits) {
    if (C != L_CIN(LAYER(m, 0)) || C > m->cmax) return -1;
    size_t plane = (size_t)m->cmax * (T > 0 ? T : 1);
    int8_t *x = ws, *y = x + plane, *blk = y + plane;
    int8_t *xt = (int8_t *)(((uintptr_t)(blk + plane) + 15) & ~(uintptr_t)15);
    memcpy(x, h, (size_t)C * T);
    return run_layers(m, x, y, blk, xt, T, logits, NULL, NULL);
}
static int run_layers(const fq_model_t *m, int8_t *x, int8_t *y, int8_t *blk, int8_t *xt, int Tc, int32_t *logits,
                      fq_layer_cb cb, void *ctx) {
    /* prefetch: the pointwise layers in order; buffer b holds the k-th one when k % 2 == b */
    int pw_next = -1, pw_k = 0;
    if (pf_on) {
        for (int i = 0; i < m->nl && pw_next < 0; i++) if (is_pw(LAYER(m, i))) pw_next = i;
        if (pw_next >= 0 && pw_bytes(LAYER(m, pw_next)) <= pf.cap) pf.start(pf.ctx, pf.buf[0], L_W(m, LAYER(m, pw_next)), pw_bytes(LAYER(m, pw_next)));
    }
    for (int i = 0; i < m->nl; i++) {
        const uint8_t *l = LAYER(m, i);
        pw_w = NULL;
        if (pf_on && i == pw_next) {
            bool here = pw_bytes(l) <= pf.cap;
            if (here) pf.wait(pf.ctx);
            int nx = -1;
            for (int j = i + 1; j < m->nl && nx < 0; j++) if (is_pw(LAYER(m, j))) nx = j;
            if (nx >= 0 && pw_bytes(LAYER(m, nx)) <= pf.cap)   /* the next one into the other buffer, while this one runs */
                pf.start(pf.ctx, pf.buf[(pw_k + 1) & 1], L_W(m, LAYER(m, nx)), pw_bytes(LAYER(m, nx)));
            if (here) pw_w = pf.buf[pw_k & 1];
            pw_next = nx; pw_k++;
        }
        int is_dw = L_GROUPS(l) > 1;
        if (is_dw) memcpy(blk, x, (size_t)L_CIN(l) * Tc);  /* block input, for the residual */
        int To = conv(m, l, x, Tc, y, blk, logits, xt);
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
    int64_t p0 = PROF_NOW();
    int C = m->ctok;                                     /* <= FQ_MAXTOK (fq_open checks) */
    double z[FQ_MAXTOK];
    for (int t = 0; t < T; t++) {
        double mx = -1e300;
        for (int c = 0; c < C; c++) {
            z[c] = (double)logits[(size_t)c * T + t] * m->lsc[c];
            if (z[c] > mx) mx = z[c];
        }
        double s = 0;
        for (int c = 0; c < C; c++) { z[c] -= mx; s += exp(z[c]); }
        double ls = log(s);
        for (int c = 0; c < C; c++) logp[(size_t)t * C + c] = (float)(z[c] - ls);
    }
    fq_prof_us[4] += (uint32_t)(PROF_NOW() - p0);
}

/* ---- streaming (fasr_q8.h) ---- */
#define FQ_CH 64                                  /* model frames per chunk */
static size_t al16(size_t n) { return (n + 15) & ~(size_t)15; }
size_t fq_stream_size(const fq_model_t *m, int Tmax) {
    size_t c = (size_t)m->cmax, h = 0;
    for (int i = 0; i < m->nl; i++) { const uint8_t *l = LAYER(m, i); if (L_GROUPS(l) > 1) h += (size_t)L_CIN(l) * (L_K(l) - 1); }
    return sizeof(fq_stream_t) + 16 * 8 + al16(40 * (size_t)Tmax) + al16(h) + 4 * al16(c * (FQ_CH + 16)) + al16(c * FQ_CH)
           + al16(208 * (size_t)FQ_CH) + al16(4 * (size_t)m->ctok * FQ_CH);
}
void fq_stream_init(fq_stream_t *st, const fq_model_t *m, void *mem, int Tmax) {
    uint8_t *p = (uint8_t *)(((uintptr_t)mem + sizeof *st + 15) & ~(uintptr_t)15);
    size_t c = (size_t)m->cmax, h = 0;
    for (int i = 0; i < m->nl; i++) { const uint8_t *l = LAYER(m, i); if (L_GROUPS(l) > 1) h += (size_t)L_CIN(l) * (L_K(l) - 1); }
    memset(st, 0, sizeof *st); st->m = m; st->Tmax = Tmax;
    st->xin = (int8_t *)p; p += al16(40 * (size_t)Tmax);
    st->hist = (int8_t *)p; p += al16(h); memset(st->hist, 0, h);   /* zeros = the left padding */
    for (int i = 0; i < 4; i++) { st->pl[i] = (int8_t *)p; p += al16(c * (FQ_CH + 16)); }
    st->xt = (int8_t *)p; p += al16(c * FQ_CH);
    st->col = (int8_t *)p; p += al16(208 * (size_t)FQ_CH);
    st->lg = (int32_t *)p;
}
/* feats: all frames so far (frame-major), nfeat of them; final: the clip is complete. Runs every model frame
 * whose inputs are all here and writes its log-probs to logp (frame-major, row t). Returns model frames done. */
int fq_stream_feed(fq_stream_t *st, const float *feats, int nfeat, bool final, float *logp) {
    const fq_model_t *m = st->m;
    pw_w = NULL;                                  /* weights read in place (no prefetch here) */
    if (nfeat > st->Tmax) nfeat = st->Tmax;
    if (nfeat > st->nin) { quant_in(m, feats, st->nin, nfeat, st->xin, st->Tmax); st->nin = nfeat; }
    const uint8_t *l0 = LAYER(m, 0);
    int num = st->nin + L_PAD(l0) - L_K(l0);
    int avail = num < 0 ? (final && st->nin > 0 ? 1 : 0) : num / L_STRIDE(l0) + 1;
    if (!final) {                                 /* not final: only outputs whose last input has arrived */
        int last_ok = st->nin - 1 - (L_K(l0) - 1 - L_PAD(l0));
        avail = last_ok < 0 ? 0 : last_ok / L_STRIDE(l0) + 1;
    }
    while (st->nout < avail) {
        int t0 = st->nout, n = avail - t0; if (n > FQ_CH) n = FQ_CH;
        int8_t *a = st->pl[0], *b = st->pl[1], *ext = st->pl[2], *blk = st->pl[3];
        if (conv_col_range(m, l0, st->xin, st->Tmax, st->nin, t0, t0 + n, a, NULL, NULL, st->col) < 0) return -1;
        int8_t *h = st->hist;
        for (int i = 1; i < m->nl; i++) {
            const uint8_t *l = LAYER(m, i);
            int C = L_CIN(l);
            if (L_GROUPS(l) > 1) {                    /* depthwise: history + chunk, outputs for the chunk only */
                int k1 = L_K(l) - 1, w = k1 + n;
                for (int ch = 0; ch < C; ch++) {
                    memcpy(ext + (size_t)ch * w, h + (size_t)ch * k1, k1);
                    memcpy(ext + (size_t)ch * w + k1, a + (size_t)ch * n, n);
                    memcpy(h + (size_t)ch * k1, ext + (size_t)ch * w + w - k1, k1);   /* last k-1 inputs for the next chunk */
                }
                memcpy(blk, a, (size_t)C * n);        /* block input, for the residual */
                conv_dw(m, l, ext, w, k1, b, blk, NULL);
                h += (size_t)C * k1;
            } else if (L_FLAGS(l) & 4) {              /* head: logits -> log-probs for these frames */
                if (conv_pw(m, l, a, n, NULL, blk, st->lg, st->xt) < 0) return -1;
                fq_logp(m, st->lg, n, logp + (size_t)t0 * m->ctok);
                break;
            } else if (conv(m, l, a, n, b, blk, NULL, st->xt) < 0) return -1;
            int8_t *t = a; a = b; b = t;
        }
        st->nout += n;
    }
    return st->nout;
}

/* ---- EXPT v1 tails ---- */
bool fq_tail_open(fq_tail_t *t, const void *blob, size_t len, const uint8_t *trunk_id) {
    const uint8_t *b = blob;
    memset(t, 0, sizeof *t);
    if (len < 48 || memcmp(b, "EXPT", 4)) return false;
    t->version = r16(b + 4); t->group_id = r16(b + 6);
    memcpy(t->trunk_id, b + 8, 8);
    t->layer_a = r16(b + 16); t->layer_b = r16(b + 18); t->e_h = (int8_t)b[20];
    uint32_t fl = r32(b + 24);
    memcpy(t->group, b + 28, 16); t->group[16] = 0;
    if (t->version != 1 || 48 + (size_t)fl > len) return false;
    if (trunk_id && memcmp(trunk_id, t->trunk_id, 8)) return false;    /* a tail for another trunk */
    if (!fq_open(&t->m, b + 48, fl)) return false;
    return t->m.e_in == t->e_h && t->m.nl == t->layer_b - t->layer_a;
}
