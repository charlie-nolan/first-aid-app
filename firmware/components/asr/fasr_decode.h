/* fasr_decode.h - word-list-constrained CTC beam search (port of pipeline/speech/fasr/decode.py:
 * decode() = nbest + calibrated reject). Phone log-probs in, n-best word ids out, shaped like
 * voice_hyp_t. The lexicon + trie come from the cart's LEXI chunk (tools/lexpack.py, LEX2). */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define FD_MAX_TOK   64
#define FD_MAX_BEAM  64
#define FD_MAX_HYPS  5
#define FD_MAX_WORDS 16
#define FD_MAX_SEQ   16384     /* word sequences alive within one frame (compacted every frame) */
#define FD_SEQ_HASH  32768
#define FD_MAX_ENT   16384     /* beam states in one frame before pruning */
#define FD_ENT_HASH  32768

typedef struct {
    const uint8_t *b; size_t len;
    uint16_t ntok; uint32_t nids, nnodes; uint8_t blank, sep;
    int beam, life_veto_conf;
    double tok_prune, word_bonus, implicit_sep, garbage_enter, garbage_phone, reject_gap0, reject_gap_scale;
    const uint8_t *words, *cls, *flags, *bias, *nodes;
} fd_lex_t;

typedef struct { double mean[8], std[8], weights[8], bias, threshold; } fd_calib_t;   /* reject_calib.json */

typedef struct {
    int n;
    struct { uint8_t len; uint16_t id[FD_MAX_WORDS]; uint8_t conf[FD_MAX_WORDS]; double p; } h[FD_MAX_HYPS];
    int reject, reject_raw;    /* reject: calibrated when a calibration is given (canonical), else raw */
    bool veto;                 /* a life word in the 5-best capped the reject at 40 */
    double gap, junk;
    int full_len, full_unk;    /* the top hypothesis before trimming to 16 words */
} fd_result_t;

typedef struct fd_ws fd_ws_t;

bool fd_open(fd_lex_t *x, const void *blob, size_t len);
const char *fd_word(const fd_lex_t *x, uint16_t id);
size_t fd_ws_size(void);
/* logp: T x ntok float log-probs (fasr_q8 fq_logp). cal may be NULL (raw reject). Returns n. */
int fd_decode(const fd_lex_t *x, const float *logp, int T, const fd_calib_t *cal, void *ws, fd_result_t *r);
/* the same in steps, for streaming: begin, frames as they arrive, finish once the clip ends (same result) */
void fd_begin(const fd_lex_t *x, void *ws);
void fd_frames(const fd_lex_t *x, const float *logp, int t0, int t1, void *ws);
int fd_finish(const fd_lex_t *x, const float *logp, int T, const fd_calib_t *cal, void *ws, fd_result_t *r);
