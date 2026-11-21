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
#pragma GCC diagnostic ignored "-Wformat-truncation"   /* long prompts / status lines are cut on purpose */
#include "voice.h"
#include "sdkconfig.h"
#if CONFIG_LIB_VOICE_DIY
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "app.h"
#include "asr.h"
#include "board.h"
#include "driver/gpio.h"
#include "driver/i2s_pdm.h"
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "esp_random.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "power.h"
#include "vbrain.h"

static const char *TAG = "voice";
#define SR 16000
#define PRE_MS 300
#define MAX_S 15
#ifndef CONFIG_LIB_VOICE_REJECT
#define CONFIG_LIB_VOICE_REJECT 51
#endif

static const cart_t *cart;
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

static SemaphoreHandle_t lock;
static vs_screen_t *staged;                 /* worker -> main (main copies it into ui.vs on MSG_VSCREEN) */
static vs_hit_t *staged_hits; static int staged_nhits; static char staged_q[64];
/* ---------- the worker: speech + form + search, one job at a time ---------- */
enum { J_SPEECH, J_OP, J_LISTEN, J_CANCEL };
typedef struct { uint8_t op, target; vb_op_t o; } job_t;
static QueueHandle_t jobs;
static bool streaming;                      /* worker only: a stream was begun for the clip being recorded */

static void post(msg_type_t t) { app_msg_t m = { .type = t }; xQueueSend(app_q, &m, pdMS_TO_TICKS(200)); }
static void words_of(const asr_hyp_t *h, char *text, size_t cap) {
    size_t o = 0; text[0] = 0;
    if (h->n) for (int i = 0; i < h->h[0].len && o + 2 < cap; i++) {
        const char *w = asr_word(h->h[0].id[i]);
        if (w) o += snprintf(text + o, cap - o, "%s%s", o ? " " : "", w);
    }
}
static void worker(void *arg) {
    static job_t j; static asr_hyp_t h; static char text[400]; static vs_screen_t s; static vs_hit_t hits[VS_HITS];
    for (;;) {
        if (xQueueReceive(jobs, &j, portMAX_DELAY) != pdTRUE) continue;
        int64_t t0 = esp_timer_get_time();
        bool ok;
        if (j.op == J_LISTEN) {                     /* TALK held: front end + model + search on the clip so far */
            if (asr_ok && asr_stream_begin() == ASR_OK) {
                streaming = true;
                while (recording) { asr_stream_feed(clip, clip_n); vTaskDelay(pdMS_TO_TICKS(30)); }
            }
            continue;
        }
        if (j.op == J_CANCEL) { if (streaming) { asr_stream_end(clip, clip_n, &h); streaming = false; } continue; }
        if (j.op == J_SPEECH) {
            asr_err_t ae = !asr_ok ? ASR_NOT_BUILT : streaming ? asr_stream_end(clip, clip_n, &h) : asr_decode(clip, clip_n, &h);
            streaming = false;
            if (ae != ASR_OK) { memset(&h, 0, sizeof h); h.reject = 100; }
            words_of(&h, text, sizeof text);
            uint32_t a, b, c; asr_last_timing(&a, &b, &c);
            ESP_LOGI(TAG, "heard (reject %d, %lu+%lu+%lu ms): %s", h.reject, (unsigned long)(a / 1000), (unsigned long)(b / 1000),
                     (unsigned long)(c / 1000), text);
            if (j.target == VT_SEARCH) {
                int n = vb_search(text, h.reject, CONFIG_LIB_VOICE_REJECT, hits, VS_HITS);
                xSemaphoreTake(lock, portMAX_DELAY);
                memcpy(staged_hits, hits, sizeof hits); staged_nhits = n; snprintf(staged_q, sizeof staged_q, "%s", text);
                xSemaphoreGive(lock);
                post(MSG_VSEARCH);
                continue;
            }
            ok = vb_said(text, h.reject, CONFIG_LIB_VOICE_REJECT, j.target == VT_NEW, &s);
        } else ok = vb_op(&j.o, &s);
        if (!ok) continue;
        xSemaphoreTake(lock, portMAX_DELAY); *staged = s; xSemaphoreGive(lock);
        post(MSG_VSCREEN);
        ESP_LOGI(TAG, "job %d: %lld ms", j.op, (esp_timer_get_time() - t0) / 1000);
    }
}


/* ---------- training mode: the PC script (firmware/tether/rec_tether.py) over USB ----------
 * PC -> board: "REC-HELLO", "PROMPT <mode>|<n>|<text>|<answer>", "STATUS <text>", "PING", "ACK <id>", "RESEND", "REC-BYE"
 * board -> PC: "REC-BETA 1", "TAKE {json}" + "D <base64>"... + "END <crc32>" (as the recorder), "BTN skip|redo|back"
 * The link drops (mode ends) after 8 s without a line from the PC. */
#include "mbedtls/base64.h"
static volatile bool rec_link;              /* training mode: the PC script is connected */
static volatile int take_state;             /* 0 none, 1 ready to send, 2 sent (waiting for ACK) */
static uint32_t take_id; static int take_peak, take_clip; static size_t take_n;
static QueueHandle_t outq;                  /* short lines to the PC */
static char rec_mode[16], rec_prompt[200], rec_answer[96], rec_status[96]; static int rec_n; static volatile int rec_on_req = -1;
static uint32_t crc32(const uint8_t *p, size_t n, uint32_t c) {
    c = ~c;
    while (n--) { c ^= *p++; for (int k = 0; k < 8; k++) c = c >> 1 ^ (0xEDB88320u & -(c & 1)); }
    return ~c;
}
static void uwrite(const char *s, size_t n) { while (n) { int w = usb_serial_jtag_write_bytes(s, n, pdMS_TO_TICKS(500)); if (w <= 0) return; s += w; n -= w; } }
static void uline(const char *s) { uwrite(s, strlen(s)); uwrite("\n", 1); }
static void rec_send_clip(void) {
    int peak = 0, clipped = 0;
    for (size_t i = 0; i < clip_n; i++) { int v = abs(clip[i]); if (v > peak) peak = v; if (v >= 32000) clipped++; }
    take_peak = peak > 32767 ? 32767 : peak; take_clip = clip_n ? (int)(1000LL * clipped / clip_n) : 0;
    take_n = clip_n; take_id++; take_state = 1;
}
static void send_take(void) {
    char hdr[256];
    snprintf(hdr, sizeof hdr, "TAKE {\"id\": %lu, \"samples\": %u, \"sr\": %d, \"peak\": %d, \"clip_per_mille\": %d, \"board\": \"%s\", \"beta\": 1}",
             (unsigned long)take_id, (unsigned)take_n, SR, take_peak, take_clip, BOARD_NAME);
    uline(hdr);
    const uint8_t *p = (const uint8_t *)clip; size_t left = take_n * 2; uint32_t c = 0;
    static unsigned char b64[4200]; size_t olen;
    while (left) {
        size_t k = left > 3072 ? 3072 : left;
        mbedtls_base64_encode(b64 + 2, sizeof b64 - 3, &olen, p, k);
        b64[0] = 'D'; b64[1] = ' '; b64[olen + 2] = '\n';
        uwrite((const char *)b64, olen + 3);
        c = crc32(p, k, c); p += k; left -= k;
    }
    char end[24]; snprintf(end, sizeof end, "END %08lx", (unsigned long)c); uline(end);
    take_state = 2;
}
static void field(char **s, char *out, size_t cap) {      /* next '|' field of *s */
    char *e = strchr(*s, '|'); size_t n = e ? (size_t)(e - *s) : strlen(*s);
    if (n >= cap) n = cap - 1;
    memcpy(out, *s, n); out[n] = 0; *s += e ? n + 1 : n;
}
static void link_line(char *l) {
    if (!strcmp(l, "REC-HELLO")) {
        if (!rec_link) { esp_log_level_set("*", ESP_LOG_NONE); rec_link = true; rec_on_req = 1; post(MSG_REC); }
        uline("REC-BETA 1");
    } else if (!rec_link) return;
    else if (!strncmp(l, "PROMPT ", 7)) {
        char *p = l + 7, n[12];
        xSemaphoreTake(lock, portMAX_DELAY);
        field(&p, rec_mode, sizeof rec_mode); field(&p, n, sizeof n); field(&p, rec_prompt, sizeof rec_prompt); field(&p, rec_answer, sizeof rec_answer);
        rec_n = atoi(n); rec_status[0] = 0;
        xSemaphoreGive(lock);
        post(MSG_REC);
    } else if (!strncmp(l, "STATUS ", 7)) {
        xSemaphoreTake(lock, portMAX_DELAY); snprintf(rec_status, sizeof rec_status, "%s", l + 7); xSemaphoreGive(lock);
        post(MSG_REC);
    } else if (!strncmp(l, "ACK ", 4)) { if (strtoul(l + 4, 0, 10) == take_id && take_state == 2) take_state = 0; }
    else if (!strcmp(l, "RESEND")) { if (take_state == 2) take_state = 1; }
    else if (!strcmp(l, "REC-BYE")) { rec_link = false; rec_on_req = 0; esp_log_level_set("*", ESP_LOG_INFO); post(MSG_REC); }
}
static void usb_task(void *arg) {
    static char line[512]; int n = 0; uint8_t in[128]; char out[64];
    int64_t last = esp_timer_get_time();
    for (;;) {
        while (xQueueReceive(outq, out, 0) == pdTRUE) uline(out);
        if (take_state == 1 && !recording) send_take();
        int got = usb_serial_jtag_read_bytes(in, sizeof in, pdMS_TO_TICKS(20));
        if (got > 0) last = esp_timer_get_time();
        for (int i = 0; i < got; i++) {
            char ch = (char)in[i];
            if (ch != '\n' && ch != '\r') { if (n < (int)sizeof line - 1) line[n++] = ch; continue; }
            line[n] = 0; if (n) link_line(line); n = 0;
        }
        if (rec_link && esp_timer_get_time() - last > 8000000) {   /* the PC script went away */
            rec_link = false; rec_on_req = 0; take_state = 0; esp_log_level_set("*", ESP_LOG_INFO); post(MSG_REC);
        }
    }
}
static void link_init(void) {
    usb_serial_jtag_driver_config_t uc = { .rx_buffer_size = 2048, .tx_buffer_size = 8192 };
    if (usb_serial_jtag_driver_install(&uc) != ESP_OK) return;
    usb_serial_jtag_vfs_use_driver();                     /* logs (printf) keep working through the driver */
    outq = xQueueCreate(8, 64);
    take_id = esp_random() % 100000;                      /* ids differ across boots (the PC keys clips by time anyway) */
    xTaskCreatePinnedToCore(usb_task, "usblink", 6144, 0, 5, 0, 0);
}
void voice_rec_button(int rc) {
    static const char *nm[] = { "", "skip", "redo", "back" };
    if (!outq || rc <= 0 || rc > 3) return;
    char b[64]; snprintf(b, sizeof b, "BTN %s", nm[rc]); xQueueSend(outq, b, pdMS_TO_TICKS(50));
}
int voice_rec_take(ui_t *u) {
    xSemaphoreTake(lock, portMAX_DELAY);
    snprintf(u->rec_mode, sizeof u->rec_mode, "%s", rec_mode); snprintf(u->rec_prompt, sizeof u->rec_prompt, "%s", rec_prompt);
    snprintf(u->rec_answer, sizeof u->rec_answer, "%s", rec_answer); snprintf(u->rec_status, sizeof u->rec_status, "%s", rec_status);
    u->rec_n = rec_n;
    xSemaphoreGive(lock);
    int r = rec_on_req; rec_on_req = -1; return r;      /* 1 on, 0 off, -1 no change */
}

/* ---------- voice.h ---------- */
bool voice_init(const cart_t *c) {
    cart = c;
    lock = xSemaphoreCreateMutex();
    const void *rom = c->base; size_t len = c->size;
    vb_init(rom, len);
    asr_err_t e = asr_open_cart(rom, len);
    asr_ok = e == ASR_OK;
    clip_cap = (size_t)SR * (MAX_S + 1);
    clip = heap_caps_malloc(clip_cap * 2, MALLOC_CAP_SPIRAM);
    staged = heap_caps_calloc(1, sizeof *staged, MALLOC_CAP_SPIRAM);
    staged_hits = heap_caps_calloc(VS_HITS, sizeof *staged_hits, MALLOC_CAP_SPIRAM);
    bool mic = clip && staged && staged_hits && mic_init();
    ESP_LOGI(TAG, "board voice: speech %s (%s, %d), form %s (%d trees), search %s, cards %s, mic %s",
             asr_ok ? "ready" : "missing", asr_engine_name(), (int)e, vb_has_form() ? "ready" : "missing", vb_ntrees(),
             vb_has_search() ? "ready" : "missing", vb_has_cards() ? "NKEY" : "missing", mic ? "ok" : "missing");
    if (!mic || !asr_ok) return false;
    jobs = xQueueCreate(4, sizeof(job_t));
    link_init();
    xTaskCreatePinnedToCore(worker, "vbrain", 32768, 0, 4, 0, 1);
    return true;
}
bool voice_has_form(void) { return vb_has_form(); }
bool voice_has_search(void) { return vb_has_search(); }
void voice_load(uint16_t tree, const cart_tags_t *ctx) { (void)tree; (void)ctx; }   /* no phrase lists: open vocabulary */
void voice_start(void) {
    if (!rx || recording) return;
    power_mic(true);
    if (!mic_on) { i2s_channel_enable(rx); mic_on = true; }
    size_t np = sizeof pre / 2, p = pre_pos;               /* pre-roll: whatever the mic had just before */
    for (size_t i = 0; i < np; i++) clip[i] = pre[(p + i) % np];
    clip_n = np; recording = true;
    if (rec_link) return;                                 /* training mode: the clip goes to the PC, not the brain */
    job_t j = { .op = J_LISTEN };
    xQueueSend(jobs, &j, 0);
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
    if (rec_link) { if (recognise) rec_send_clip(); return; }
    if (!recognise) { job_t c = { .op = J_CANCEL }; xQueueSend(jobs, &c, pdMS_TO_TICKS(100)); return; }
    job_t j = { .op = J_SPEECH, .target = (uint8_t)target };
    xQueueSend(jobs, &j, pdMS_TO_TICKS(100));
}
void voice_form_op(int vf, const ui_t *u) {
    job_t j = { .op = J_OP };
    if (vb_op_of(vf, u, &j.o)) xQueueSend(jobs, &j, pdMS_TO_TICKS(100));
}
void voice_take(vs_screen_t *s, vs_hit_t *hits, int16_t *nhits, char *q, size_t qcap) {
    xSemaphoreTake(lock, portMAX_DELAY);
    if (s) *s = *staged;
    if (hits) { memcpy(hits, staged_hits, sizeof *hits * VS_HITS); *nhits = (int16_t)staged_nhits; snprintf(q, qcap, "%s", staged_q); }
    xSemaphoreGive(lock);
}
#endif
