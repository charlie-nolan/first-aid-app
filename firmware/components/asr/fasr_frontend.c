/* fasr_frontend.c - see fasr_frontend.h */
#include "fasr_frontend.h"
#include <math.h>
#include <string.h>

static float win[FE_WIN];
static float mel[FE_NMEL][FE_NFFT / 2 + 1];
static double cs[FE_NFFT / 2], sn[FE_NFFT / 2];
static float csf[FE_NFFT / 2], snf[FE_NFFT / 2];
static short mlo[FE_NMEL], mhi[FE_NMEL];   /* nonzero span of each mel row (adding 0 changes nothing) */
static int ready;

static double hz2mel(double f) { return 2595.0 * log10(1.0 + f / 700.0); }
static double mel2hz(double m) { return 700.0 * (pow(10.0, m / 2595.0) - 1.0); }

static void setup(void) {
    for (int i = 0; i < FE_WIN; i++)               /* np.hanning(WIN + 1)[:WIN], as float32 */
        win[i] = (float)(0.5 - 0.5 * cos(2.0 * M_PI * i / FE_WIN));
    double lo = hz2mel(60.0), hi = hz2mel(7600.0), pts[FE_NMEL + 2];
    for (int i = 0; i < FE_NMEL + 2; i++) pts[i] = mel2hz(lo + (hi - lo) * i / (FE_NMEL + 1));
    for (int m = 0; m < FE_NMEL; m++)
        for (int k = 0; k <= FE_NFFT / 2; k++) {
            double f = (double)k * FE_SR / FE_NFFT;
            double a = (f - pts[m]) / (pts[m + 1] - pts[m]), b = (pts[m + 2] - f) / (pts[m + 2] - pts[m + 1]);
            double v = a < b ? a : b;
            mel[m][k] = (float)(v > 0 ? v : 0);
        }
    for (int m = 0; m < FE_NMEL; m++) {
        mlo[m] = FE_NFFT / 2 + 1; mhi[m] = -1;
        for (int k = 0; k <= FE_NFFT / 2; k++) if (mel[m][k] != 0.0f) { if (k < mlo[m]) mlo[m] = k; mhi[m] = k; }
    }
    for (int i = 0; i < FE_NFFT / 2; i++) { cs[i] = cos(-2.0 * M_PI * i / FE_NFFT); sn[i] = sin(-2.0 * M_PI * i / FE_NFFT); csf[i] = (float)cs[i]; snf[i] = (float)sn[i]; }
    ready = 1;
}

#ifndef FE_FAST
static void fft(double *re, double *im) {            /* in place, radix 2, n = 512 */
    int n = FE_NFFT;
    for (int i = 1, j = 0; i < n; i++) {
        int bit = n >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) { double t = re[i]; re[i] = re[j]; re[j] = t; t = im[i]; im[i] = im[j]; im[j] = t; }
    }
    for (int len = 2; len <= n; len <<= 1) {
        int step = n / len;
        for (int i = 0; i < n; i += len)
            for (int k = 0; k < len / 2; k++) {
                double wr = cs[k * step], wi = sn[k * step];
                double ur = re[i + k], ui = im[i + k];
                double vr = re[i + k + len / 2] * wr - im[i + k + len / 2] * wi;
                double vi = re[i + k + len / 2] * wi + im[i + k + len / 2] * wr;
                re[i + k] = ur + vr; im[i + k] = ui + vi;
                re[i + k + len / 2] = ur - vr; im[i + k + len / 2] = ui - vi;
            }
    }
}

#endif

/* ln(x) for x > 0 from plain float operations (FE_FAST), so the PC (glibc) and the chip (newlib) give the same bits:
 * x = m * 2^e with m in [sqrt(1/2), sqrt(2)), ln(m) = 2 atanh(s), s = (m - 1) / (m + 1), |s| < 0.172, series to s^9
 * (error ~1e-9, below float precision), plus e * ln 2 in two parts. Exponent handling is exact (frexpf). */
#ifdef FE_FAST
static float fe_logf(float x) {
    int e; float m = frexpf(x, &e);
    if (m < 0.70710678f) { m *= 2.0f; e--; }
    float f = m - 1.0f, s = f / (2.0f + f), z = s * s;
    float p = 1.0f + z * (0.33333333f + z * (0.2f + z * (0.14285714f + z * 0.11111111f)));
    return 2.0f * s * p + (float)e * 0.693145751953125f + (float)e * 1.428606765330187e-06f;
}

/* float32 FFT (FE_FAST): the chip has a hardware float unit but no double one (double is ~60 cycles an op).
 * Plain C built with -ffp-contract=off, so the PC and the chip give the same bits; vs numpy's float64
 * FFT the features move a little (fe_test reports by how much). */
static void fftf(float *re, float *im) {
    int n = FE_NFFT;
    for (int i = 1, j = 0; i < n; i++) {
        int bit = n >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) { float t = re[i]; re[i] = re[j]; re[j] = t; t = im[i]; im[i] = im[j]; im[j] = t; }
    }
    for (int len = 2; len <= n; len <<= 1) {
        int step = n / len, h = len / 2;
        for (int i = 0; i < n; i += len)
            for (int k = 0; k < h; k++) {
                float wr = csf[k * step], wi = snf[k * step];
                float *ar = re + i + k, *ai = im + i + k, *br = ar + h, *bi = ai + h;
                float vr = *br * wr - *bi * wi, vi = *br * wi + *bi * wr;
                float ur = *ar, ui = *ai;
                *ar = ur + vr; *ai = ui + vi; *br = ur - vr; *bi = ui - vi;
            }
    }
}

#endif

int fe_frames(int n) { if (n < FE_WIN) n = FE_WIN; return 1 + (n - FE_WIN) / FE_HOP; }

int fe_logmel(const int16_t *pcm, int n, float *out) { int T = fe_frames(n); fe_logmel_range(pcm, n, 0, T, out); return T; }
void fe_logmel_range(const int16_t *pcm, int n, int t0, int T, float *out) {
    if (!ready) setup();
    double pw[FE_NFFT / 2 + 1];
#ifdef FE_FAST
    float re[FE_NFFT], im[FE_NFFT];
#else
    double re[FE_NFFT], im[FE_NFFT];
#endif
    for (int t = t0; t < T; t++) {
        for (int i = 0; i < FE_NFFT; i++) {
            int si = t * FE_HOP + i;
            float x = (i < FE_WIN && si < n) ? (float)pcm[si] / 32768.0f : 0.0f;   /* float32 as numpy */
            re[i] = i < FE_WIN ? x * win[i] : 0; im[i] = 0;
        }
#ifdef FE_FAST
        fftf(re, im);
        float pf[FE_NFFT / 2 + 1];
        for (int k = 0; k <= FE_NFFT / 2; k++) pf[k] = re[k] * re[k] + im[k] * im[k];
        for (int m = 0; m < FE_NMEL; m++) {
            float s = 0;
            for (int k = mlo[m]; k <= mhi[m]; k++) s += pf[k] * mel[m][k];
            out[(size_t)t * FE_NMEL + m] = fe_logf(s + 1e-6f);
        }
        continue;
#else
        fft(re, im);
        for (int k = 0; k <= FE_NFFT / 2; k++) pw[k] = re[k] * re[k] + im[k] * im[k];
#endif
        for (int m = 0; m < FE_NMEL; m++) {
            double s = 0;
            for (int k = mlo[m]; k <= mhi[m]; k++) s += pw[k] * mel[m][k];
            out[(size_t)t * FE_NMEL + m] = (float)log(s + 1e-6);
        }
    }
}
