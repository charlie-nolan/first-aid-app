/* voice_diy.c - board voice (Kconfig LIB_VOICE_DIY): our own speech engine (components/asr), the
 * fill-in-the-blanks form (components/vform, the same screens as vr.form) and keyword search
 * (vform_search), all on the board. No PC, no MultiNet. The beta firmware's voice.
 *
 * Hold TALK: the mic records (with a short pre-roll and tail), the worker task decodes the clip and
 * runs the form engine on the words, and posts MSG_VSCREEN (a form screen, the likely cards, "say it
 * again" or a card) or MSG_VSEARCH (keyword search tiles) to the app queue. Picks, SKIP and opening a
 * likely card go the same way (voice_form_op). Cards are opened in the reader through the cart's NKEY
 * chunk ("tree/card" -> cart node); they are the real, cited cards.
 *
 * Ported from the voice tether (firmware/tether/main/tether.c, board-brain + standalone builds). */
#include "voice.h"
#include "sdkconfig.h"
#if CONFIG_LIB_VOICE_DIY
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "app.h"
#include "asr.h"
#include "board.h"
#include "cJSON.h"
#include "driver/gpio.h"
#include "driver/i2s_pdm.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "power.h"
#include "vform_search.h"
#include "vform_show.h"

static const char *TAG = "voice";
#define SR 16000
#define PRE_MS 300
#define MAX_S 15
#ifndef CONFIG_LIB_VOICE_REJECT
#define CONFIG_LIB_VOICE_REJECT 51
#endif

static const cart_t *cart;
static const uint8_t *nkey;                 /* NK01: sorted "tree/card" -> cart node */
static fs_index_t sidx; static bool sidx_ok;
static fp_sess_t sess; static fp_screen_t scr; static bool form_ok;
static bool asr_ok;

/* ---------- mic (same code as the recorder / tether: read both PDM slots, keep the one that moves) ---------- */
static i2s_chan_handle_t rx;
static int16_t *clip; static size_t clip_cap; static volatile size_t clip_n;
static volatile bool recording, mic_on;
static int16_t pre[SR * PRE_MS / 1000]; static size_t pre_pos;
static uint32_t act[2]; static volatile int mic_slot;
static void mic_task(void *arg) {
    int16_t raw[512];
    for (;;) {
        if (!mic_on) { vTaskDelay(pdMS_TO_TICKS(20)); continue; }
        size_t got = 0;
        if (i2s_channel_read(rx, raw, sizeof raw, &got, pdMS_TO_TICKS(100)) != ESP_OK) continue;
        size_t n = got / 4;
        uint32_t d[2] = {0, 0};
        for (size_t i = 1; i < n; i++) for (int c = 0; c < 2; c++) d[c] += abs(raw[2 * i + c] - raw[2 * i - 2 + c]);
        for (int c = 0; c < 2; c++) act[c] = act[c] - (act[c] >> 4) + (d[c] >> 4);
        if (act[mic_slot ^ 1] > 4 * act[mic_slot] + 64) mic_slot ^= 1;
        for (size_t i = 0; i < n; i++) {
            int16_t v = raw[2 * i + mic_slot];
            if (recording) { if (clip_n < clip_cap) clip[clip_n++] = v; }
            else { pre[pre_pos] = v; pre_pos = (pre_pos + 1) % (sizeof pre / 2); }
        }
    }
}
static bool mic_init(void) {
    if (PIN_MIC_CLK < 0 || PIN_MIC_DATA < 0) return false;
    i2s_chan_config_t cc = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    if (i2s_new_channel(&cc, NULL, &rx) != ESP_OK) return false;
    i2s_pdm_rx_config_t pc = {
        .clk_cfg = I2S_PDM_RX_CLK_DEFAULT_CONFIG(SR),
        .slot_cfg = I2S_PDM_RX_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = { .clk = PIN_MIC_CLK, .din = PIN_MIC_DATA },
    };
    if (i2s_channel_init_pdm_rx_mode(rx, &pc) != ESP_OK) return false;
    xTaskCreatePinnedToCore(mic_task, "mic", 4096, 0, 7, 0, 1);
    return true;
}

/* ---------- the cart's chunks ---------- */
static uint32_t rd32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
static uint16_t node_of(const char *key) {
    if (!nkey || !key || !key[0]) return CART_NONE;
    uint32_t lo = 0, hi = rd32(nkey + 4);
    while (lo < hi) {
        uint32_t mid = (lo + hi) / 2;
        const uint8_t *e = nkey + 8 + 12 * mid;
        int c = strcmp((const char *)nkey + rd32(e), key);
        if (!c) return (uint16_t)(e[8] | e[9] << 8);
        if (c < 0) lo = mid + 1; else hi = mid;
    }
    return CART_NONE;
}

/* ---------- a form screen (vform_show JSON) into the ui's struct ---------- */
static void cpy(char *dst, size_t cap, const cJSON *o, const char *name) {
    const cJSON *v = name ? cJSON_GetObjectItem(o, name) : o;
    dst[0] = 0;
    if (cJSON_IsString(v)) { strncpy(dst, v->valuestring, cap - 1); dst[cap - 1] = 0; }
}
static vs_screen_t *staged;                 /* worker -> main (main copies it into ui.vs on MSG_VSCREEN) */
static vs_hit_t *staged_hits; static int staged_nhits; static char staged_q[64];
static SemaphoreHandle_t lock;
static bool parse_screen(const char *js, vs_screen_t *s) {
    cJSON *r = cJSON_Parse(js);
    if (!r) return false;
    memset(s, 0, sizeof *s);
    const cJSON *v = cJSON_GetObjectItem(r, "seq"); s->seq = cJSON_IsNumber(v) ? v->valueint : 0;
    char kind[16]; cpy(kind, sizeof kind, r, "kind");
    s->kind = !strcmp(kind, "form") ? VS_FORM : !strcmp(kind, "card") ? VS_CARD : !strcmp(kind, "skip") ? VS_SKIP
            : !strcmp(kind, "again") ? VS_AGAIN : VS_START;
    cpy(s->heard, sizeof s->heard, r, "heard"); cpy(s->msg, sizeof s->msg, r, "msg");
    const cJSON *f;
    cJSON_ArrayForEach(f, cJSON_GetObjectItem(r, "fields")) {
        if (s->nf >= VS_FIELDS) break;
        vs_field_t *d = &s->f[s->nf];
        cpy(d->key, sizeof d->key, f, "key"); cpy(d->q, sizeof d->q, f, "q");
        const cJSON *c;
        cJSON_ArrayForEach(c, cJSON_GetObjectItem(f, "choices")) {
            if (d->nch >= VS_CHOICES) break;
            vs_choice_t *h = &d->ch[d->nch];
            cpy(h->label, sizeof h->label, c, "label"); cpy(h->value, sizeof h->value, c, "value"); cpy(h->node, sizeof h->node, c, "node");
            if (!h->label[0]) snprintf(h->label, sizeof h->label, "%s", h->value[0] ? h->value : h->node);
            d->nch++;
        }
        if (d->nch) s->nf++;
    }
    const cJSON *it;
    cJSON_ArrayForEach(it, cJSON_GetObjectItem(r, "cards")) {
        if (s->ncards >= VS_CARDS) break;
        vs_card_t *k = &s->cards[s->ncards++];
        cpy(k->title, sizeof k->title, it, "title"); cpy(k->node, sizeof k->node, it, "node");
        k->cart_node = node_of(k->node);
    }
    const cJSON *c = cJSON_GetObjectItem(r, "card");
    if (c) { cpy(s->card.title, sizeof s->card.title, c, "title"); cpy(s->card.node, sizeof s->card.node, c, "node"); }
    s->card.cart_node = node_of(s->card.node);
    cJSON_Delete(r);
    return true;
}

/* ---------- the worker: speech + form + search, one job at a time ---------- */
enum { J_SPEECH, J_PICK, J_OPEN, J_SKIP, J_NEW };
typedef struct { uint8_t op, target; char a[80], b[48], c[80]; bool plain; } job_t;
static QueueHandle_t jobs;
static char *out;                           /* one SHOW frame */
#define OUT_CAP 16384

static void post(msg_type_t t) { app_msg_t m = { .type = t }; xQueueSend(app_q, &m, pdMS_TO_TICKS(200)); }
static void publish(void) {
    static vs_screen_t s;
    if (!parse_screen(out, &s)) { ESP_LOGW(TAG, "bad form frame"); return; }
    xSemaphoreTake(lock, portMAX_DELAY); *staged = s; xSemaphoreGive(lock);
    post(MSG_VSCREEN);
}
static void words_of(const asr_hyp_t *h, char *text, size_t cap) {
    size_t o = 0; text[0] = 0;
    if (h->n) for (int i = 0; i < h->h[0].len && o + 2 < cap; i++) {
        const char *w = asr_word(h->h[0].id[i]);
        if (w) o += snprintf(text + o, cap - o, "%s%s", o ? " " : "", w);
    }
}
static void search(const char *q, int reject) {
    static fs_hit_t hits[FS_MAX_HITS];
    xSemaphoreTake(lock, portMAX_DELAY);
    snprintf(staged_q, sizeof staged_q, "%s", q);
    if (reject >= CONFIG_LIB_VOICE_REJECT || !q[0]) staged_nhits = -1;
    else {
        int n = sidx_ok ? fs_search(&sidx, q, hits) : 0;
        if (n > VS_HITS) n = VS_HITS;
        for (int i = 0; i < n; i++) {
            const char *lab = strstr(hits[i].title, " > "); lab = lab ? lab + 3 : hits[i].title;
            snprintf(staged_hits[i].title, sizeof staged_hits[i].title, "%s", lab);
            staged_hits[i].cart_node = node_of(hits[i].node); staged_hits[i].life = hits[i].life;
        }
        staged_nhits = n;
    }
    xSemaphoreGive(lock);
    post(MSG_VSEARCH);
}
static void worker(void *arg) {
    static job_t j; static asr_hyp_t h; static char text[400];
    for (;;) {
        if (xQueueReceive(jobs, &j, portMAX_DELAY) != pdTRUE) continue;
        int64_t t0 = esp_timer_get_time();
        if (j.op == J_SPEECH) {
            if (!asr_ok || asr_decode(clip, clip_n, &h) != ASR_OK) { memset(&h, 0, sizeof h); h.reject = 100; }
            words_of(&h, text, sizeof text);
            uint32_t a, b, c; asr_last_timing(&a, &b, &c);
            ESP_LOGI(TAG, "heard (reject %d, %lu+%lu+%lu ms): %s", h.reject, (unsigned long)(a / 1000), (unsigned long)(b / 1000),
                     (unsigned long)(c / 1000), text);
            if (j.target == VT_SEARCH) { search(text, h.reject); continue; }
            if (!form_ok) continue;
            if (j.target == VT_NEW) { fp_screen_new(&scr, out, OUT_CAP); }
            if (h.reject >= CONFIG_LIB_VOICE_REJECT) fp_screen_again(&scr, text, h.reject, out, OUT_CAP);
            else fp_screen_said(&scr, text, out, OUT_CAP);
        } else if (!form_ok) continue;
        else if (j.op == J_PICK) fp_screen_picked(&scr, j.a, j.plain ? NULL : j.b, j.plain ? j.c : NULL, out, OUT_CAP);
        else if (j.op == J_OPEN) fp_screen_open(&scr, j.a, out, OUT_CAP);
        else if (j.op == J_SKIP) fp_screen_skip(&scr, out, OUT_CAP);
        else fp_screen_new(&scr, out, OUT_CAP);
        publish();
        ESP_LOGI(TAG, "job %d: %lld ms", j.op, (esp_timer_get_time() - t0) / 1000);
    }
}

/* ---------- voice.h ---------- */
bool voice_init(const cart_t *c) {
    cart = c;
    lock = xSemaphoreCreateMutex();
    const void *rom = c->base; size_t len = c->size;
    size_t n = 0;
    const uint8_t *nk = fp_cart_chunk(rom, len, "NKEY", &n);
    nkey = nk && n >= 8 && !memcmp(nk, "NK01", 4) ? nk : NULL;
    const void *sr = fp_cart_chunk(rom, len, "SRCH", &n);
    sidx_ok = sr && fs_open(&sidx, sr, n);
    const void *fp = fp_cart_chunk(rom, len, "FORM", &n);
    form_ok = fp && fp_sess_init(&sess, fp, n);
    if (form_ok) { fp_screen_init(&scr, &sess); scr.form_hint = "Hold TALK and say the answers, or pick with buttons."; }
    asr_err_t e = asr_open_cart(rom, len);
    asr_ok = e == ASR_OK;
    clip_cap = (size_t)SR * (MAX_S + 1);
    clip = heap_caps_malloc(clip_cap * 2, MALLOC_CAP_SPIRAM);
    staged = heap_caps_calloc(1, sizeof *staged, MALLOC_CAP_SPIRAM);
    staged_hits = heap_caps_calloc(VS_HITS, sizeof *staged_hits, MALLOC_CAP_SPIRAM);
    out = heap_caps_malloc(OUT_CAP, MALLOC_CAP_SPIRAM);
    bool mic = clip && staged && staged_hits && out && mic_init();
    ESP_LOGI(TAG, "board voice: speech %s (%s, %d), form %s (%d trees), search %s, cards %s, mic %s",
             asr_ok ? "ready" : "missing", asr_engine_name(), (int)e, form_ok ? "ready" : "missing", form_ok ? sess.ntrees : 0,
             sidx_ok ? "ready" : "missing", nkey ? "NKEY" : "missing", mic ? "ok" : "missing");
    if (!mic || !asr_ok) return false;
    jobs = xQueueCreate(4, sizeof(job_t));
    xTaskCreatePinnedToCore(worker, "vbrain", 32768, 0, 4, 0, 1);
    return true;
}
bool voice_has_form(void) { return form_ok; }
bool voice_has_search(void) { return sidx_ok; }
void voice_load(uint16_t tree, const cart_tags_t *ctx) { (void)tree; (void)ctx; }   /* no phrase lists: open vocabulary */
void voice_start(void) {
    if (!rx || recording) return;
    power_mic(true);
    if (!mic_on) { i2s_channel_enable(rx); mic_on = true; }
    size_t np = sizeof pre / 2, p = pre_pos;               /* pre-roll: whatever the mic had just before */
    for (size_t i = 0; i < np; i++) clip[i] = pre[(p + i) % np];
    clip_n = np; recording = true;
}
static void mic_off(void) {
    if (!mic_on) return;
    mic_on = false; vTaskDelay(pdMS_TO_TICKS(30));
    i2s_channel_disable(rx); power_mic(false);
}
void voice_stop(bool recognise) { voice_stop_to(recognise, VT_NEW); }
void voice_stop_to(bool recognise, int target) {
    if (!recording) return;
    if (recognise) vTaskDelay(pdMS_TO_TICKS(250));          /* keep the last word's tail */
    recording = false;
    mic_off();
    if (!recognise) return;
    job_t j = { .op = J_SPEECH, .target = (uint8_t)target };
    xQueueSend(jobs, &j, pdMS_TO_TICKS(100));
}
void voice_form_op(int vf, const ui_t *u) {
    const vs_screen_t *v = u->vs;
    job_t j = { 0 };
    if (vf == VF_PICK && v->kind == VS_FORM && v->nf) {
        const vs_field_t *f = &v->f[u->vs_f]; const vs_choice_t *c = &f->ch[u->vs_c];
        j.op = J_PICK; j.plain = !c->value[0] && c->node[0];
        snprintf(j.a, sizeof j.a, "%s", f->key); snprintf(j.b, sizeof j.b, "%s", c->value); snprintf(j.c, sizeof j.c, "%s", c->node);
    } else if (vf == VF_OPEN && v->kind == VS_SKIP && v->ncards) { j.op = J_OPEN; snprintf(j.a, sizeof j.a, "%s", v->cards[u->vs_card].node); }
    else if (vf == VF_SKIP) j.op = J_SKIP;
    else return;
    xQueueSend(jobs, &j, pdMS_TO_TICKS(100));
}
void voice_take(vs_screen_t *s, vs_hit_t *hits, int16_t *nhits, char *q, size_t qcap) {
    xSemaphoreTake(lock, portMAX_DELAY);
    if (s) *s = *staged;
    if (hits) { memcpy(hits, staged_hits, sizeof *hits * VS_HITS); *nhits = (int16_t)staged_nhits; snprintf(q, qcap, "%s", staged_q); }
    xSemaphoreGive(lock);
}
#endif
