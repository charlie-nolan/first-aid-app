/* asr.h - one interface for the three speech engines (Kconfig "Speech recognizer engine").
 * A clip in (16 kHz mono int16, TALK held), word hypotheses out. Nothing generative: the output is
 * ids from the cartridge lexicon, which the router (vrouter) and the form (vform) turn into cards.
 * Contract with the speech thread (2026-11-16/17): ids < 4096, 0 = <unk>, n-best <= 5, conf and
 * reject 0-100. The DIY and ESP-DL engines are empty slots until the int8 export lands. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "sdkconfig.h"

#define ASR_MAX_HYP 5
#define ASR_MAX_WORDS 16

typedef struct {
    uint8_t n;
    struct { uint8_t len; uint16_t id[ASR_MAX_WORDS]; uint8_t conf[ASR_MAX_WORDS]; } h[ASR_MAX_HYP];
    uint8_t reject;
} asr_hyp_t;

typedef enum { ASR_OK = 0, ASR_NOT_BUILT, ASR_NO_MODEL, ASR_NO_MEMORY } asr_err_t;

/* model: the engine's blob in flash (cartridge or its own partition), len its size. */
asr_err_t asr_open(const void *model, size_t len);
/* Optional: limit the search to one sector's words (the per-question word lists in the pack). */
void asr_set_words(const uint16_t *ids, int n);
/* Decode one whole clip. */
asr_err_t asr_decode(const int16_t *pcm, size_t samples, asr_hyp_t *out);
const char *asr_engine_name(void);
