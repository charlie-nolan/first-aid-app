/* asr.c - engine dispatch. The DIY int8 and ESP-DL engines are placeholders that report
 * ASR_NOT_BUILT until their code lands; MultiNet stays in main/voice.c for now (the fallback). */
#include "asr.h"
#include <string.h>

#if CONFIG_ASR_ENGINE_DIY
const char *asr_engine_name(void) { return "diy-int8"; }
#elif CONFIG_ASR_ENGINE_ESPDL
const char *asr_engine_name(void) { return "esp-dl"; }
#else
const char *asr_engine_name(void) { return "multinet"; }
#endif

asr_err_t asr_open(const void *model, size_t len) {
    (void)model; (void)len;
    return ASR_NOT_BUILT;
}

void asr_set_words(const uint16_t *ids, int n) { (void)ids; (void)n; }

asr_err_t asr_decode(const int16_t *pcm, size_t samples, asr_hyp_t *out) {
    (void)pcm; (void)samples;
    memset(out, 0, sizeof *out);
    out->reject = 100;
    return ASR_NOT_BUILT;
}
