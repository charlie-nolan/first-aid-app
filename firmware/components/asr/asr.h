/* asr.h - one interface for the three speech engines (Kconfig "Speech recognizer engine").
 * A clip in (16 kHz mono int16, TALK held), word hypotheses out. Nothing generative: the output is
 * ids from the cartridge lexicon, which the router (vrouter) and the form (vform) turn into cards.
 * DIY engine (ASR_ENGINE_DIY): log-mel (fasr_frontend) -> int8 conv model (fasr_q8) -> word-list
 * beam search + calibrated reject (fasr_decode), all checked bit for bit against the speech
 * thread's Python. The model, lexicon and calibration ride in the cart (ASRM, LEXI, ASRC chunks). */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "sdkconfig.h"

#define ASR_MAX_HYP 5
#define ASR_MAX_WORDS 16
#define ASR_MAX_SECONDS 15

typedef struct {
    uint8_t n;
    struct { uint8_t len; uint16_t id[ASR_MAX_WORDS]; uint8_t conf[ASR_MAX_WORDS]; } h[ASR_MAX_HYP];
    uint8_t reject;            /* calibrated (0-100, reject above 50), raw fit-gap if no ASRC */
    uint8_t reject_raw;
    bool veto;                 /* a life word in the 5-best capped the reject at 40 */
} asr_hyp_t;

typedef enum { ASR_OK = 0, ASR_NOT_BUILT, ASR_NO_MODEL, ASR_NO_MEMORY } asr_err_t;

/* The mapped cart (library partition): finds ASRM, LEXI, ASRC. */
asr_err_t asr_open_cart(const void *rom, size_t len);
/* Decode one whole clip. */
asr_err_t asr_decode(const int16_t *pcm, size_t samples, asr_hyp_t *out);
/* Word for a lexicon id (NULL for <unk> / unknown). */
const char *asr_word(uint16_t id);
const char *asr_engine_name(void);
/* Time of the last decode, microseconds: front end, model, search. */
void asr_last_timing(uint32_t *fe_us, uint32_t *model_us, uint32_t *search_us);
