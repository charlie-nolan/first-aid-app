/* fasr_frontend.h - log-mel features exactly as pipeline/speech/fasr/features.py:
 * 16 kHz, 400-sample periodic Hann window, hop 160, 512-point FFT, 40 mel bands 60-7600 Hz,
 * ln(power + 1e-6). Double precision inside (numpy's FFT and matmul run in float64). */
#pragma once
#include <stdint.h>

#define FE_SR 16000
#define FE_WIN 400
#define FE_HOP 160
#define FE_NFFT 512
#define FE_NMEL 40

/* Frames for n samples (a clip shorter than one window is padded to one). */
int fe_frames(int n);
/* pcm: n int16 samples -> out: frames x 40 float. Returns frames. */
int fe_logmel(const int16_t *pcm, int n, float *out);
/* Frames [t0, t1) only (streaming: frame t needs samples t*HOP .. t*HOP + WIN, so fe_frames(n) frames are
 * final as soon as n samples are in; the same values as fe_logmel). */
void fe_logmel_range(const int16_t *pcm, int n, int t0, int t1, float *out);
