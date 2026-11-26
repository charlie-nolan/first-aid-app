/* fasr_q8.h - the DIY int8 engine for the speech thread's acoustic model (FASR-Q8 v1,
 * pipeline/speech/fasr/export_int8.py: its numpy forward_q is the bit-exact reference).
 * Plain C, no ESP-IDF: also builds on the PC for the golden test (host_test.c). */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define FQ_MAXTOK 128
typedef struct {
    const uint8_t *b;          /* the ASRM blob (tools/asrpack.py), read in place */
    uint16_t nl;               /* layers */
    int16_t e_in;              /* input exponent */
    const uint8_t *mu, *sd;    /* 40 floats each (little endian) */
    const uint8_t *layers, *w; /* layer table, weights block */
    int ctok;                  /* output tokens (head cout) */
    int cmax;                  /* widest layer */
    double lsc[FQ_MAXTOK];     /* 2^-exponent of each head token (fq_logp) */
} fq_model_t;

bool fq_open(fq_model_t *m, const void *blob, size_t len);
/* Frames out for T feature frames (stem stride 2). */
int fq_out_len(const fq_model_t *m, int T);
/* Bytes of work space fq_forward needs for T feature frames. */
size_t fq_ws_size(const fq_model_t *m, int T);
/* feats: T x 40 float (log-mel). logits: ctok x T' int32 (head accumulators, exponent per token in
 * fq_logit_exp). cb (may be NULL) sees each layer's int8 output (C x T'), for the golden test. */
typedef void (*fq_layer_cb)(void *ctx, int layer, const int8_t *x, int c, int t);
int fq_forward(const fq_model_t *m, const float *feats, int T, void *ws, int32_t *logits, fq_layer_cb cb, void *ctx);
/* Weight prefetch (optional): two 16-byte-aligned buffers of `cap` bytes in fast RAM. Before each pointwise layer
 * the engine waits for its weights (wait), then asks for the next pointwise layer's (start: copy n bytes src ->
 * dst, may return at once and copy in the background, e.g. on the other core) and reads the layer from the
 * buffer. Layers bigger than cap read from the model in place. Same results (only where the bytes are read). */
typedef struct {
    int8_t *buf[2]; size_t cap;
    void (*start)(void *ctx, void *dst, const void *src, size_t n);
    void (*wait)(void *ctx);
    void *ctx;
} fq_prefetch_t;
void fq_set_prefetch(const fq_prefetch_t *p);    /* NULL: off */

/* Head output exponent of token c: real logit = logits * 2^-e. */
int fq_logit_exp(const fq_model_t *m, int c);
/* Head logits -> log-softmax, frame-major (T' x ctok), as logits_to_logp. */
void fq_logp(const fq_model_t *m, const int32_t *logits, int T, float *logp);

/* time per stage, microseconds, summed since boot (0 on the PC): quantise, pointwise, depthwise, other, softmax */
extern uint32_t fq_prof_us[5];

/* Streaming: feed the log-mel frames as they arrive (TALK still held); each model frame is computed once, from
 * the same inputs and with the same integer sums as fq_forward, so the log-probs are identical. */
typedef struct {
    const fq_model_t *m; int Tmax, nin, nout;
    int8_t *xin, *hist, *pl[4], *xt, *col; int32_t *lg;
    int h_layer; int8_t *h; int h_stride;   /* Stage 3: keep layer h_layer's output (C x frames, row stride h_stride) */
} fq_stream_t;
size_t fq_stream_size(const fq_model_t *m, int Tmax);          /* bytes for fq_stream_init's mem (Tmax feature frames) */
void fq_stream_init(fq_stream_t *st, const fq_model_t *m, void *mem, int Tmax);
int fq_stream_feed(fq_stream_t *st, const float *feats, int nfeat, bool final, float *logp);
/* Stage 3: also keep the trunk's output H (the output of base layer `layer`, i.e. a tail's layer_a - 1) while
 * streaming: h = C x stride int8 (stride >= the most model frames). Call after fq_stream_init. */
void fq_stream_keep_h(fq_stream_t *st, int layer, int8_t *h, int stride);

/* ---- Stage 3: shared trunk + per-group expert tails (speech thread, fasr/tail.py EXPT v1) ----
 * The base model runs once; the int8 output of its layer layer_a-1 (H, channel-major C x T') is kept. A tail is
 * an FQ81 blob of only the base layers layer_a..n-1 (fine-tuned per group), run straight on H. */
typedef struct {
    uint16_t version, group_id, layer_a, layer_b;
    uint8_t trunk_id[8];
    int8_t e_h;
    char group[17];
    fq_model_t m;              /* the tail layers (fq_open on the embedded FQ81) */
} fq_tail_t;
/* Read an EXPT v1 tail. trunk_id (8 bytes) = the loaded base trunk's id: a tail for another trunk is refused
 * (NULL skips the check). false = bad header / FQ81 / trunk. */
bool fq_tail_open(fq_tail_t *t, const void *blob, size_t len, const uint8_t *trunk_id);
/* Run every layer of m on an int8 input h (C x T, channel-major, exponent = m's e_in) instead of log-mel:
 * a tail on the trunk's saved H. Same work space as fq_forward (fq_ws_size(m, T)). Returns output frames. */
int fq_forward_h(const fq_model_t *m, const int8_t *h, int C, int T, void *ws, int32_t *logits);
