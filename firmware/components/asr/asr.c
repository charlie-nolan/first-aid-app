/* asr.c - engine dispatch. DIY int8: fasr_frontend + fasr_q8 + fasr_decode. ESP-DL is a placeholder
 * (ASR_NOT_BUILT). MultiNet stays in main/voice.c (the fallback). */
#include "asr.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fasr_decode.h"
#include "fasr_frontend.h"
#include "fasr_q8.h"
#ifdef ESP_PLATFORM
#include "esp_heap_caps.h"
#include "esp_timer.h"
static void *big(size_t n) { return heap_caps_malloc(n, MALLOC_CAP_SPIRAM); }
static void *fast_tmp(size_t n) { return heap_caps_malloc(n, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT); }
static int64_t now_us(void) { return esp_timer_get_time(); }
/* weight prefetch (fq_prefetch_t): copies run on core 0 while the model runs on core 1 */
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#ifdef CONFIG_ASR_PREFETCH
typedef struct { void *d; const void *s; size_t n; } pf_job_t;
static QueueHandle_t pf_q; static SemaphoreHandle_t pf_done; static volatile int pf_busy;
static void pf_task(void *a) {
    pf_job_t j;
    for (;;) if (xQueueReceive(pf_q, &j, portMAX_DELAY) == pdTRUE) { memcpy(j.d, j.s, j.n); pf_busy = 0; xSemaphoreGive(pf_done); }
}
static void pf_wait(void *c) { (void)c; if (pf_busy) xSemaphoreTake(pf_done, portMAX_DELAY); }
static void pf_start(void *c, void *d, const void *s, size_t n) {
    pf_wait(c);                                      /* one copy at a time */
    xSemaphoreTake(pf_done, 0);                      /* clear a stale give */
    pf_busy = 1; pf_job_t j = { d, s, n }; xQueueSend(pf_q, &j, portMAX_DELAY);
}
#endif
static void prefetch_setup(const fq_model_t *m) {
    size_t cap = 0;
    for (int i = 0; i < m->nl; i++) {
        const uint8_t *l = m->layers + 32 * i;
        int cin = l[0] | l[1] << 8, cout = l[2] | l[3] << 8, k = l[4] | l[5] << 8, g = l[10] | l[11] << 8;
        if (k == 1 && g == 1 && (size_t)cin * cout > cap) cap = (size_t)cin * cout;
    }
#ifdef CONFIG_ASR_PREFETCH
    static fq_prefetch_t p;
    p.buf[0] = heap_caps_aligned_alloc(16, cap, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    p.buf[1] = heap_caps_aligned_alloc(16, cap, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (!p.buf[0] || !p.buf[1]) { free(p.buf[0]); free(p.buf[1]); return; }   /* no room: weights read in place */
    p.cap = cap; p.start = pf_start; p.wait = pf_wait;
    pf_q = xQueueCreate(1, sizeof(pf_job_t)); pf_done = xSemaphoreCreateBinary();
    xTaskCreatePinnedToCore(pf_task, "asr_pf", 2048, 0, 6, 0, 0);
    fq_set_prefetch(&p);
    printf("ASR prefetch on: 2 x %u bytes internal\n", (unsigned)cap);
#else
    (void)cap;
#endif
}
#else
#include <time.h>
static void *big(size_t n) { return malloc(n); }
static void *fast_tmp(size_t n) { (void)n; return NULL; }
static void prefetch_setup(const fq_model_t *m) { (void)m; }
static int64_t now_us(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1000000LL + t.tv_nsec / 1000; }
#endif

#if CONFIG_ASR_ENGINE_ESPDL
const char *asr_engine_name(void) { return "esp-dl"; }
#elif CONFIG_ASR_ENGINE_MULTINET
const char *asr_engine_name(void) { return "multinet"; }
#else
const char *asr_engine_name(void) { return "diy-int8"; }
#endif

/* cart chunk by tag (same as vform's fp_cart_chunk; kept here so asr has no dependency) */
static const void *chunk(const void *rom, size_t len, const char tag[4], size_t *size) {
    const uint8_t *b = rom;
    if (!b || len < 28 || memcmp(b, "CRT1", 4)) return NULL;
    uint16_t n = (uint16_t)(b[6] | b[7] << 8);
    for (uint16_t i = 0; i < n && 28u + 12u * (i + 1) <= len; i++) {
        const uint8_t *d = b + 28 + 12 * i;
        uint32_t off = d[4] | d[5] << 8 | d[6] << 16 | (uint32_t)d[7] << 24, sz = d[8] | d[9] << 8 | d[10] << 16 | (uint32_t)d[11] << 24;
        if (memcmp(d, tag, 4) || off + sz > len) continue;
        *size = sz;
        return b + off;
    }
    return NULL;
}

static fq_model_t M; static fd_lex_t X; static fd_calib_t CAL; static bool have_cal, ready;
static float *feats, *logp; static void *ws_model, *ws_dec; static int32_t *logits;
static uint32_t t_fe, t_model, t_search;

asr_err_t asr_open_cart(const void *rom, size_t len) {
#if !CONFIG_ASR_ENGINE_DIY
    (void)rom; (void)len; (void)chunk;
    return ASR_NOT_BUILT;
#else
    size_t ml = 0, ll = 0, cl = 0;
    const void *m = chunk(rom, len, "ASRM", &ml), *l = chunk(rom, len, "LEXI", &ll);
    const uint8_t *c = chunk(rom, len, "ASRC", &cl);
    if (!m || !l || !fq_open(&M, m, ml) || !fd_open(&X, l, ll)) return ASR_NO_MODEL;
    have_cal = c && cl >= 4 + 26 * 8 && !memcmp(c, "RJC1", 4);
    if (have_cal) {
        double v[26]; memcpy(v, c + 4, sizeof v);
        memcpy(CAL.mean, v, 64); memcpy(CAL.std, v + 8, 64); memcpy(CAL.weights, v + 16, 64);
        CAL.bias = v[24]; CAL.threshold = v[25];
    }
    int T = fe_frames(16000 * ASR_MAX_SECONDS), To = fq_out_len(&M, T);
    feats = big(sizeof(float) * 40 * T); logp = big(sizeof(float) * M.ctok * To);
    logits = big(sizeof(int32_t) * M.ctok * To);
    ws_model = big(fq_ws_size(&M, T)); ws_dec = big(fd_ws_size());
    if (!feats || !logp || !logits || !ws_model || !ws_dec) return ASR_NO_MEMORY;
    prefetch_setup(&M);
    ready = true;
    return ASR_OK;
#endif
}

asr_err_t asr_decode(const int16_t *pcm, size_t samples, asr_hyp_t *out) {
    memset(out, 0, sizeof *out);
    out->reject = 100;
    if (!ready) return ASR_NOT_BUILT;
    if (samples > (size_t)16000 * ASR_MAX_SECONDS) samples = (size_t)16000 * ASR_MAX_SECONDS;
    int64_t t0 = now_us();
    int T = fe_logmel(pcm, (int)samples, feats);
    int64_t t1 = now_us();
    /* the work planes for THIS clip in internal RAM when they fit (~4 x 256 x frames bytes), else the
     * PSRAM ones sized for ASR_MAX_SECONDS */
    void *ws = fast_tmp(fq_ws_size(&M, T)), *wsm = ws ? ws : ws_model;
    int To = fq_forward(&M, feats, T, wsm, logits, NULL, NULL);
    if (ws) free(ws);
    fq_logp(&M, logits, To, logp);
    int64_t t2 = now_us();
    static fd_result_t r;
    fd_decode(&X, logp, To, have_cal ? &CAL : NULL, ws_dec, &r);
    int64_t t3 = now_us();
    t_fe = (uint32_t)(t1 - t0); t_model = (uint32_t)(t2 - t1); t_search = (uint32_t)(t3 - t2);
    out->n = (uint8_t)r.n;
    for (int h = 0; h < r.n && h < ASR_MAX_HYP; h++) {
        out->h[h].len = r.h[h].len;
        memcpy(out->h[h].id, r.h[h].id, sizeof out->h[h].id);
        memcpy(out->h[h].conf, r.h[h].conf, sizeof out->h[h].conf);
    }
    out->reject = (uint8_t)r.reject; out->reject_raw = (uint8_t)r.reject_raw; out->veto = r.veto;
    return ASR_OK;
}

const char *asr_word(uint16_t id) { return ready ? fd_word(&X, id) : NULL; }

void asr_last_timing(uint32_t *fe_us, uint32_t *model_us, uint32_t *search_us) {
    *fe_us = t_fe; *model_us = t_model; *search_us = t_search;
}
