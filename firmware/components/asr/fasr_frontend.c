/* fasr_frontend.c - see fasr_frontend.h */
#include "fasr_frontend.h"
#include <math.h>
#include <string.h>

static float win[FE_WIN];
static float mel[FE_NMEL][FE_NFFT / 2 + 1];
static double cs[FE_NFFT / 2], sn[FE_NFFT / 2];
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
    for (int i = 0; i < FE_NFFT / 2; i++) { cs[i] = cos(-2.0 * M_PI * i / FE_NFFT); sn[i] = sin(-2.0 * M_PI * i / FE_NFFT); }
    ready = 1;
}

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

int fe_frames(int n) { if (n < FE_WIN) n = FE_WIN; return 1 + (n - FE_WIN) / FE_HOP; }

int fe_logmel(const int16_t *pcm, int n, float *out) {
    if (!ready) setup();
    int T = fe_frames(n);
    double re[FE_NFFT], im[FE_NFFT], pw[FE_NFFT / 2 + 1];
    for (int t = 0; t < T; t++) {
        for (int i = 0; i < FE_NFFT; i++) {
            int si = t * FE_HOP + i;
            float x = (i < FE_WIN && si < n) ? (float)pcm[si] / 32768.0f : 0.0f;   /* float32 as numpy */
            re[i] = i < FE_WIN ? (double)(x * win[i]) : 0.0; im[i] = 0.0;
        }
        fft(re, im);
        for (int k = 0; k <= FE_NFFT / 2; k++) pw[k] = re[k] * re[k] + im[k] * im[k];
        for (int m = 0; m < FE_NMEL; m++) {
            double s = 0;
            for (int k = 0; k <= FE_NFFT / 2; k++) s += pw[k] * mel[m][k];
            out[(size_t)t * FE_NMEL + m] = (float)log(s + 1e-6);
        }
    }
    return T;
}
