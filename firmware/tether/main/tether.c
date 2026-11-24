/* tether.c - voice tether: a rough bench firmware for trying the voice flow, with the PC as the brain.
 *
 * Hold TALK and speak; the clip goes over USB to pc_bridge.py (firmware/tether), which runs the
 * speech model and the fill-in-the-blanks form (pipeline/voice_router/vr/form.py) and sends back
 * one small JSON screen. The board draws it: the open fields with their choices, the SKIP list,
 * or the card that opens (title + cites; never card text). Answers can be picked with the buttons,
 * so every screen works without speaking (buttons first).
 *
 * Buttons (plain S3 bench: TALK D0, BACK D1, DOWN D2):
 *   TALK  hold = speak (300 ms in = recording; 500 ms of pre-roll keeps the first word)
 *         tap  = choose the highlighted choice / open the highlighted card
 *   DOWN  tap  = next choice (runs on into the next field);  hold = SKIP (cards the blanks allow)
 *   BACK  tap  = previous choice;                            hold = new problem
 *
 * USB protocol (text lines; see firmware/tether/README.md):
 *   PC -> board:  "HELLO", "ACK <id>", "RESEND", "SHOW <json>"
 *   board -> PC:  "VR-TETHER 1 <pending>", "TAKE {json}" + "D <base64>"... + "END <crc32>" (as the recorder),
 *                 "PICK {json}", "LOG <text>"
 */
#pragma GCC diagnostic ignored "-Wformat-truncation"   /* long labels are cut on purpose */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "board.h"
#include "cart.h"
#include "epd.h"
#include "cJSON.h"
#include "driver/gpio.h"
#include "driver/i2s_pdm.h"
#include "driver/usb_serial_jtag.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_partition.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"
#include "nvs_flash.h"
#if CONFIG_TETHER_BOARD_BRAIN
#include "vform_show.h"
#endif
#include "vform_search.h"
#if CONFIG_TETHER_STANDALONE
#include "asr.h"
#endif

#ifdef CONFIG_REC_EPD
#define HAVE_EPD 1
#else
#define HAVE_EPD 0
#endif

#define SR 16000
#ifdef CONFIG_TETHER_REJECT
#define CONFIG_TETHER_REJECT_ANY CONFIG_TETHER_REJECT
#else
#define CONFIG_TETHER_REJECT_ANY 51            /* calibrated reject: ask again at 51+ (pc_bridge --reject) */
#endif
#define PRE_MS 500
#define HOLD_MS 300
#define MAX_TAKE_S 15
static const char *TAG = "tether";
static SemaphoreHandle_t lock;
static const void *cart_rom; static size_t cart_len;   /* the mapped library partition (set in app_main) */
/* keyword search mode state (shared by the USB task, the board speech and the main loop) */
static bool search_mode, s_card;
static volatile bool s_words_ready; static char s_words[400]; static int s_reject;
enum { B_TALK, B_BACK, B_DOWN, B_UP, B_WIN_L, B_WIN_R };
static const int BTN[6] = { PIN_BTN_TALK, PIN_BTN_BACK, PIN_BTN_DOWN, PIN_BTN_UP, PIN_BTN_WIN_L, PIN_BTN_WIN_R };

/* ---------- one clip at a time in PSRAM, kept until the PC ACKs it ---------- */
static int16_t *clip; static size_t clip_cap;
static volatile size_t clip_n; static volatile bool recording;
static volatile int take_state;                 /* 0 none, 1 ready to send, 2 sent (waiting ACK) */
static uint32_t take_id = 1; static int16_t take_peak;

static i2s_chan_handle_t rx;
static int16_t pre[SR * PRE_MS / 1000]; static size_t pre_pos;
static uint32_t act[2]; static volatile int mic_slot;

/* Same mic code as the recorder (rec.c): read both PDM slots, keep the one that moves. */
static void mic_task(void *arg) {
    int16_t raw[512], buf[256];
    for (;;) {
        size_t got = 0;
        if (i2s_channel_read(rx, raw, sizeof raw, &got, pdMS_TO_TICKS(100)) != ESP_OK) continue;
        size_t n = got / 4;
        uint32_t d[2] = {0, 0};
        for (size_t i = 1; i < n; i++) for (int c = 0; c < 2; c++) d[c] += abs(raw[2 * i + c] - raw[2 * i - 2 + c]);
        for (int c = 0; c < 2; c++) act[c] = act[c] - (act[c] >> 4) + (d[c] >> 4);
        if (act[mic_slot ^ 1] > 4 * act[mic_slot] + 64) mic_slot ^= 1;
        for (size_t i = 0; i < n; i++) buf[i] = raw[2 * i + mic_slot];
        if (recording) {
            size_t room = clip_cap - clip_n, k = n < room ? n : room;
            memcpy(clip + clip_n, buf, k * 2); clip_n += k;
        } else {
            for (size_t i = 0; i < n; i++) { pre[pre_pos] = buf[i]; pre_pos = (pre_pos + 1) % (sizeof pre / 2); }
        }
    }
}

static void mic_init(void) {
    if (CONFIG_REC_MIC_PWR >= 0) { gpio_set_direction(CONFIG_REC_MIC_PWR, GPIO_MODE_OUTPUT); gpio_set_level(CONFIG_REC_MIC_PWR, 1); }
    i2s_chan_config_t cc = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    ESP_ERROR_CHECK(i2s_new_channel(&cc, NULL, &rx));
    i2s_pdm_rx_config_t pc = {
        .clk_cfg = I2S_PDM_RX_CLK_DEFAULT_CONFIG(SR),
        .slot_cfg = I2S_PDM_RX_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = { .clk = CONFIG_REC_MIC_CLK, .din = CONFIG_REC_MIC_DATA },
    };
    ESP_ERROR_CHECK(i2s_channel_init_pdm_rx_mode(rx, &pc));
    ESP_ERROR_CHECK(i2s_channel_enable(rx));
    xTaskCreatePinnedToCore(mic_task, "mic", 4096, 0, 7, 0, 1);
}

static void rec_start(void) {
    size_t np = sizeof pre / 2, p = pre_pos;
    for (size_t i = 0; i < np; i++) clip[i] = pre[(p + i) % np];
    clip_n = np; take_state = 0; recording = true;
}
static void rec_stop(void) {
    vTaskDelay(pdMS_TO_TICKS(250));            /* keep the last word's tail */
    recording = false;
    vTaskDelay(pdMS_TO_TICKS(20));
    int peak = 0;
    for (size_t i = 0; i < clip_n; i++) { int v = abs(clip[i]); if (v > peak) peak = v; }
    take_peak = (int16_t)(peak > 32767 ? 32767 : peak);
    take_id++;
    take_state = 1;
}

/* ---------- screen description from the PC ---------- */
enum { K_START, K_FORM, K_CARD, K_SKIP, K_AGAIN };
typedef struct { char label[48], value[48], node[80]; } choice_t;  /* value "" = plain tile: node is its target */
typedef struct { char key[40], q[96]; int nch; choice_t ch[12]; } field_t;
typedef struct { char title[120], node[80]; } cardref_t;
typedef struct {
    int seq, kind, test;              /* test: the bench scenario number (0 = none) */
    char heard[128], msg[128];
    int nf; field_t f[6];
    int ntop; cardref_t top[3];
    cardref_t card; char cites[200];
    int ncards; cardref_t cards[6];
} screen_t;
static int cur_test;                    /* board brain: the bench test number from a "TEST n" line */
static screen_t scr_in;                 /* written by the USB task under the lock */
static volatile uint32_t scr_ver;

static void cpy(char *dst, size_t cap, const cJSON *o, const char *name) {
    const cJSON *v = name ? cJSON_GetObjectItem(o, name) : o;
    dst[0] = 0;
    if (cJSON_IsString(v)) { strncpy(dst, v->valuestring, cap - 1); dst[cap - 1] = 0; }
    else if (cJSON_IsNumber(v)) snprintf(dst, cap, "%g", v->valuedouble);
}
static void refs(cardref_t *out, int *n, int max, const cJSON *arr) {
    *n = 0; const cJSON *it;
    cJSON_ArrayForEach(it, arr) { if (*n >= max) break; cpy(out[*n].title, sizeof out->title, it, "title"); cpy(out[*n].node, sizeof out->node, it, "node"); (*n)++; }
}
static bool parse_show(const char *js) {
    cJSON *r = cJSON_Parse(js);
    if (!r) return false;
    static screen_t s; memset(&s, 0, sizeof s);
    const cJSON *v = cJSON_GetObjectItem(r, "seq"); s.seq = cJSON_IsNumber(v) ? v->valueint : 0;
    char kind[16]; cpy(kind, sizeof kind, r, "kind");
    s.kind = !strcmp(kind, "form") ? K_FORM : !strcmp(kind, "card") ? K_CARD : !strcmp(kind, "skip") ? K_SKIP
           : !strcmp(kind, "again") ? K_AGAIN : K_START;
    v = cJSON_GetObjectItem(r, "test"); s.test = cJSON_IsNumber(v) ? v->valueint : cur_test;   /* board brain: TEST line */
    cpy(s.heard, sizeof s.heard, r, "heard"); cpy(s.msg, sizeof s.msg, r, "msg");
    const cJSON *f;
    cJSON_ArrayForEach(f, cJSON_GetObjectItem(r, "fields")) {
        if (s.nf >= 6) break;
        field_t *d = &s.f[s.nf];
        cpy(d->key, sizeof d->key, f, "key"); cpy(d->q, sizeof d->q, f, "q");
        const cJSON *c;
        cJSON_ArrayForEach(c, cJSON_GetObjectItem(f, "choices")) {
            if (d->nch >= 12) break;
            cpy(d->ch[d->nch].label, sizeof d->ch->label, c, "label"); cpy(d->ch[d->nch].value, sizeof d->ch->value, c, "value"); cpy(d->ch[d->nch].node, sizeof d->ch->node, c, "node");
            if (!d->ch[d->nch].label[0]) strcpy(d->ch[d->nch].label, d->ch[d->nch].value[0] ? d->ch[d->nch].value : d->ch[d->nch].node);
            d->nch++;
        }
        if (d->nch) s.nf++;
    }
    refs(s.top, &s.ntop, 3, cJSON_GetObjectItem(r, "top"));
    refs(s.cards, &s.ncards, 6, cJSON_GetObjectItem(r, "cards"));
    const cJSON *c = cJSON_GetObjectItem(r, "card");
    if (c) {
        cpy(s.card.title, sizeof s.card.title, c, "title"); cpy(s.card.node, sizeof s.card.node, c, "node");
        const cJSON *ci; size_t o = 0;
        cJSON_ArrayForEach(ci, cJSON_GetObjectItem(c, "cites")) {
            char t[80]; cpy(t, sizeof t, ci, NULL);
            o += snprintf(s.cites + o, o < sizeof s.cites ? sizeof s.cites - o : 0, "%s%s", o ? "; " : "", t);
            if (o >= sizeof s.cites) break;
        }
    }
    cJSON_Delete(r);
    xSemaphoreTake(lock, portMAX_DELAY); scr_in = s; scr_ver++; xSemaphoreGive(lock);
    return true;
}

/* ---------- USB link (the USB task is the only writer) ---------- */
static QueueHandle_t outq;              /* short lines from the main loop (PICK, LOG) */
static uint32_t crc32(const uint8_t *p, size_t n, uint32_t c) {
    c = ~c;
    while (n--) { c ^= *p++; for (int k = 0; k < 8; k++) c = c >> 1 ^ (0xEDB88320u & -(c & 1)); }
    return ~c;
}
static void usb_write(const char *s, size_t n) {
    while (n) { int w = usb_serial_jtag_write_bytes(s, n, pdMS_TO_TICKS(500)); if (w <= 0) return; s += w; n -= w; }
}
static void usb_line(const char *s) { usb_write(s, strlen(s)); usb_write("\n", 1); }
static void send_line(const char *s) { char b[256]; strncpy(b, s, sizeof b - 1); b[sizeof b - 1] = 0; xQueueSend(outq, b, pdMS_TO_TICKS(100)); }

/* ---------- board brain (Kconfig TETHER_BOARD_BRAIN): the form runs here, in C (components/vform) ----------
 * The PC only turns speech into words ("WORDS {json}"); every screen is built on the board with the
 * same bytes the PC's vr.form would send, drawn through parse_show(), and echoed as "SHOWN <json>"
 * so the bridge can log it (and diff it against the Python). Taps never leave the board. */
#if CONFIG_TETHER_BOARD_BRAIN
extern const uint8_t fpk_start[] asm("_binary_form_fpk_start");
extern const uint8_t fpk_end[] asm("_binary_form_fpk_end");
enum { BR_SAID, BR_PICK, BR_SKIP, BR_OPEN, BR_NEW, BR_AGAIN, BR_RESET };
typedef struct { int op; char a[400], b[96], c[96]; bool b_null; } brain_req_t;
static QueueHandle_t brainq;
static char *shown; static volatile bool shown_pending;   /* sent by the USB task (the only writer) */
static bool brain_ok;
static const char *brain_src = "none";
static void brain_post(int op, const char *a, const char *b, const char *c) {
    static brain_req_t r;                                  /* posters: main loop and USB task, never both at once */
    memset(&r, 0, sizeof r); r.op = op;
    if (a) strncpy(r.a, a, sizeof r.a - 1);
    if (b) strncpy(r.b, b, sizeof r.b - 1); else r.b_null = true;
    if (c) strncpy(r.c, c, sizeof r.c - 1);
    xQueueSend(brainq, &r, pdMS_TO_TICKS(200));
}
static bool parse_show(const char *js);
static void brain_task(void *arg) {
    static fp_sess_t s; static fp_screen_t sc;
    char *out = heap_caps_malloc(16384, MALLOC_CAP_SPIRAM);
    size_t fl = 0;                                        /* the cart's FORM chunk (personal trees) wins */
    const void *fp = cart_rom ? fp_cart_chunk(cart_rom, cart_len, "FORM", &fl) : NULL;
    brain_ok = out && fp && fp_sess_init(&s, fp, fl);
    if (brain_ok) brain_src = "cart";
    else { brain_ok = out && fp_sess_init(&s, fpk_start, fpk_end - fpk_start); brain_src = brain_ok ? "built-in" : "none"; }
    { char m[96]; snprintf(m, sizeof m, "LOG brain form pack: %s, %d trees", brain_src, s.ntrees); send_line(m); }
    fp_screen_init(&sc, &s);
    sc.form_hint = "Hold TALK and say the answers, or pick with buttons.";   /* as pc_bridge.py */
    static brain_req_t r;
    for (;;) {
        if (xQueueReceive(brainq, &r, portMAX_DELAY) != pdTRUE || !brain_ok) continue;
        if (r.op == BR_RESET) {                            /* the bridge (re)started: seq from 1 again */
            fp_reset(&s); fp_screen_init(&sc, &s);
            sc.form_hint = "Hold TALK and say the answers, or pick with buttons.";
            continue;
        }
        int64_t t = esp_timer_get_time();
        switch (r.op) {
        case BR_SAID: fp_screen_said(&sc, r.a, out, 16384); break;
        case BR_PICK: fp_screen_picked(&sc, r.a, r.b_null ? NULL : r.b, r.c[0] ? r.c : NULL, out, 16384); break;
        case BR_SKIP: fp_screen_skip(&sc, out, 16384); break;
        case BR_OPEN: fp_screen_open(&sc, r.a, out, 16384); break;
        case BR_NEW: fp_screen_new(&sc, out, 16384); break;
        default: fp_screen_again(&sc, r.a, atoi(r.b), out, 16384);   /* speech reject */
        }
        t = esp_timer_get_time() - t;
        parse_show(out);
        while (shown_pending) vTaskDelay(pdMS_TO_TICKS(5));
        snprintf(shown, 16400, "SHOWN %s", out);
        char ms[48]; snprintf(ms, sizeof ms, "LOG brain %lld ms", t / 1000);
        send_line(ms);
        shown_pending = true;
    }
}
#if CONFIG_TETHER_STANDALONE
/* ---------- board only: the speech engine on the board turns the clip into words (components/asr) ----------
 * The cart carries LEXI (word id -> word), ASRM (model) and ASRC (reject calibration). Until the
 * engine is built (asr_open says ASR_NOT_BUILT) the clip goes to the PC as before, if one is there. */
static bool asr_ready;
static void asr_setup(void) {
    asr_err_t e = asr_open_cart(cart_rom, cart_len);
    asr_ready = e == ASR_OK;
    char t[120];
    snprintf(t, sizeof t, "LOG board speech: %s (%s, error %d)", asr_ready ? "ready" : "not available", asr_engine_name(), (int)e);
    send_line(t);
}
/* Decode the clip on the board. true = handled (the brain gets the words); false = send it to the PC. */
static bool asr_take(void) {
    if (!asr_ready) return false;
    static asr_hyp_t h;
    if (asr_decode(clip, clip_n, &h) != ASR_OK) return false;
    static char text[400];
    size_t o = 0;
    text[0] = 0;
    if (h.n) for (int i = 0; i < h.h[0].len && o + 2 < sizeof text; i++) {
        const char *w = asr_word(h.h[0].id[i]);
        if (w) o += snprintf(text + o, sizeof text - o, "%s%s", o ? " " : "", w);
    }
    char n[12]; snprintf(n, sizeof n, "%d", h.reject);
    if (search_mode) {                                    /* search: the words go to the grid, not the form */
        xSemaphoreTake(lock, portMAX_DELAY);
        snprintf(s_words, sizeof s_words, "%s", text); s_reject = h.reject; s_words_ready = true;
        xSemaphoreGive(lock);
    } else if (h.reject >= CONFIG_TETHER_REJECT) brain_post(BR_AGAIN, text, n, NULL);
    else brain_post(BR_SAID, text, "", NULL);
    uint32_t a, b, c; asr_last_timing(&a, &b, &c);
    char lg[460]; snprintf(lg, sizeof lg, "LOG board heard (reject %d, %lu+%lu+%lu ms): %s", h.reject,
                           (unsigned long)(a / 1000), (unsigned long)(b / 1000), (unsigned long)(c / 1000), text); send_line(lg);
    return true;
}
#endif
static void brain_words(const char *js) {                  /* WORDS {"hyps": [...], "reject": N} */
    cJSON *r = cJSON_Parse(js);
    if (!r) return;
    const cJSON *h = cJSON_GetArrayItem(cJSON_GetObjectItem(r, "hyps"), 0), *rj = cJSON_GetObjectItem(r, "reject");
    int reject = cJSON_IsNumber(rj) ? rj->valueint : 0;
    if (reject >= CONFIG_TETHER_REJECT) { char n[12]; snprintf(n, sizeof n, "%d", reject); brain_post(BR_AGAIN, cJSON_IsString(h) ? h->valuestring : "", n, NULL); }
    else brain_post(BR_SAID, cJSON_IsString(h) ? h->valuestring : "", "", NULL);
    cJSON_Delete(r);
}
#endif

static void send_take(void) {
    char hdr[256];
    snprintf(hdr, sizeof hdr, "TAKE {\"id\": %lu, \"mode\": \"free\", \"prompt\": -1, \"text\": \"\", \"samples\": %u, "
             "\"sr\": %d, \"peak\": %d, \"clip_per_mille\": 0, \"redo_of\": -1, \"board\": \"%s\", \"speaker\": \"me\", \"tether\": 1}",
             (unsigned long)take_id, (unsigned)clip_n, SR, take_peak, BOARD_NAME);
    usb_line(hdr);
    const uint8_t *p = (const uint8_t *)clip;
    size_t left = clip_n * 2; uint32_t c = 0;
    static unsigned char b64[4200]; size_t olen;
    while (left) {
        size_t k = left > 3072 ? 3072 : left;
        mbedtls_base64_encode(b64 + 2, sizeof b64 - 3, &olen, p, k);
        b64[0] = 'D'; b64[1] = ' '; b64[olen + 2] = '\n';
        usb_write((const char *)b64, olen + 3);
        c = crc32(p, k, c); p += k; left -= k;
    }
    char end[24]; snprintf(end, sizeof end, "END %08lx", (unsigned long)c); usb_line(end);
    take_state = 2;
}

static volatile bool host_seen;
/* the last drawn framebuffer, sent as "FB <base64>" so the PC can save real screenshots */
static uint8_t fb_out[32 * 122]; static volatile bool fb_dirty;
static void send_fb(void) {
    static uint8_t copy[32 * 122]; static unsigned char b64[5300]; size_t olen;
    xSemaphoreTake(lock, portMAX_DELAY); memcpy(copy, fb_out, sizeof copy); fb_dirty = false; xSemaphoreGive(lock);
    memcpy(b64, "FB ", 3);
    mbedtls_base64_encode(b64 + 3, sizeof b64 - 4, &olen, copy, sizeof copy);
    b64[olen + 3] = '\n';
    usb_write((const char *)b64, olen + 4);
}
static void handle(char *line) {
    host_seen = true;
    if (!strcmp(line, "HELLO")) { char s[40]; snprintf(s, sizeof s, "VR-TETHER 1 %d", take_state ? 1 : 0); usb_line(s); }
    else if (!strncmp(line, "ACK ", 4)) { if (strtoul(line + 4, 0, 10) == take_id && take_state == 2) take_state = 0; }
    else if (!strcmp(line, "RESEND")) { if (take_state == 2) take_state = 1; }
    else if (!strncmp(line, "SHOW ", 5)) { if (!parse_show(line + 5)) usb_line("LOG bad SHOW json"); }
    else if (!strncmp(line, "WORDS ", 6) && search_mode) {          /* the PC heard the search word */
        cJSON *r = cJSON_Parse(line + 6);
        const cJSON *h = r ? cJSON_GetArrayItem(cJSON_GetObjectItem(r, "hyps"), 0) : NULL, *rj = r ? cJSON_GetObjectItem(r, "reject") : NULL;
        xSemaphoreTake(lock, portMAX_DELAY);
        snprintf(s_words, sizeof s_words, "%s", cJSON_IsString(h) ? h->valuestring : "");
        s_reject = cJSON_IsNumber(rj) ? rj->valueint : 0; s_words_ready = true;
        xSemaphoreGive(lock);
        cJSON_Delete(r);
    }
#if CONFIG_TETHER_BOARD_BRAIN
    else if (!strncmp(line, "WORDS ", 6)) brain_words(line + 6);
    else if (!strcmp(line, "BRAIN-RESET")) brain_post(BR_RESET, NULL, "", NULL);
    else if (!strncmp(line, "TEST ", 5)) cur_test = atoi(line + 5);
#endif
}
static void usb_task(void *arg) {
    static char line[8192]; int n = 0; uint8_t in[256]; char out[256];
    for (;;) {
        while (xQueueReceive(outq, out, 0) == pdTRUE) usb_line(out);
        if (take_state == 1 && !recording) send_take();
        if (fb_dirty && host_seen) send_fb();
#if CONFIG_TETHER_BOARD_BRAIN
        if (shown_pending) { usb_line(shown); shown_pending = false; }
#endif
        int got = usb_serial_jtag_read_bytes(in, sizeof in, pdMS_TO_TICKS(20));
        for (int i = 0; i < got; i++) {
            char ch = (char)in[i];
            if (ch != '\n' && ch != '\r') { if (n < (int)sizeof line - 1) line[n++] = ch; continue; }
            line[n] = 0;
            if (n) handle(line);
            n = 0;
        }
    }
}

/* ---------- drawing ---------- */
static cart_t cart; static bool have_cart;
static uint8_t fbits[32 * 122];
static cart_fb_t fb = { fbits, 32, 250, 122 };
static int screens;
static unsigned S_SMALL = 0, S_MED = 1, S_BIG = 2;

static int text_w(unsigned st, const char *s) { return have_cart ? cart_draw_str(&cart, st, 0, 0, 0, 0, s) : 0; }
/* one line, cut with ".." to fit w */
static void line_fit(unsigned st, int bold, int x, int y, int w, const char *s) {
    if (!have_cart) return;
    char b[160]; strncpy(b, s, sizeof b - 1); b[sizeof b - 1] = 0;
    size_t n = strlen(b);
    if (cart_draw_str(&cart, st, bold, 0, 0, 0, b) > w) {
        while (n > 2) { n--; b[n] = 0; char t[164]; snprintf(t, sizeof t, "%s..", b); if (cart_draw_str(&cart, st, bold, 0, 0, 0, t) <= w) { strcpy(b, t); break; } }
    }
    cart_draw_str(&cart, st, bold, &fb, x, y, b);
}
/* word wrap into a box; returns the y below the last line */
static int wrap(unsigned style, int x, int y, int w, int h, const char *s) {
    if (!have_cart) return y;
    int lh = cart_font_line_height(&cart, style), cy = y; char line[128]; size_t i = 0, len = strlen(s);
    while (i < len && cy + lh <= y + h) {
        size_t n = 0, brk = 0;
        while (i + n < len && n < sizeof line - 1) {
            memcpy(line, s + i, n + 1); line[n + 1] = 0;
            if (cart_draw_str(&cart, style, 0, 0, 0, 0, line) > w) break;
            n++; if (i + n < len && s[i + n] == ' ') brk = n;
        }
        if (i + n < len && brk) n = brk;
        if (!n) n = 1;
        memcpy(line, s + i, n); line[n] = 0;
        cart_draw_str(&cart, style, 0, &fb, x, cy + lh - lh / 4, line);
        cy += lh; i += n; while (i < len && s[i] == ' ') i++;
    }
    return cy;
}
static void hline(int y) { for (int x = 0; x < 250; x++) fbits[y * 32 + x / 8] |= 0x80 >> (x % 8); }
static void box(int x0, int y0, int x1, int y1) {
    for (int x = x0; x <= x1; x++) { fbits[y0 * 32 + x / 8] |= 0x80 >> (x % 8); fbits[y1 * 32 + x / 8] |= 0x80 >> (x % 8); }
    for (int y = y0; y <= y1; y++) { fbits[y * 32 + x0 / 8] |= 0x80 >> (x0 % 8); fbits[y * 32 + x1 / 8] |= 0x80 >> (x1 % 8); }
}

/* the board's own copy of the screen + its cursor */
static screen_t S; static int cur_f, cur_c, cur_card;
static char status[96];
static bool rec_dot;

/* ---------- full card view: a voice result opens the real card from the library ----------
 * The cart's NKEY chunk (firmware/tools/nkeypack.py, checked by nkey_check) maps "tree/card" (the form
 * engine's node id) to the cart node. Pages: each passage (word-wrapped, cart_page), then the figure.
 * DOWN / BACK tap turn pages. Without NKEY the old title + cites screen is shown. */
static const uint8_t *nkey;
static int card_page_i;
static uint32_t rd32le(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
static int card_node_of(const char *key) {
    if (!nkey) return -1;
    uint32_t lo = 0, hi = rd32le(nkey + 4);
    while (lo < hi) {
        uint32_t mid = (lo + hi) / 2;
        const uint8_t *e = nkey + 8 + 12 * mid;
        int c = strcmp((const char *)nkey + rd32le(e), key);
        if (!c) return e[8] | e[9] << 8;
        if (c < 0) lo = mid + 1; else hi = mid;
    }
    return -1;
}
typedef struct { int seg, idx, sub; } cpage_t;      /* seg 0 = passage idx page sub, 1 = figure */
static char card_scratch[6144];
static int card_pages(const cart_node_t *n, int bx, int by, int bw, int bh, cpage_t *out, int max) {
    int k = 0;
    for (int i = 0; i < n->n_passages + n->n_lookalikes && k < max; i++) {
        cart_passage_t ps = cart_node_passage(&cart, n, (unsigned)i);
        int np = cart_page(&cart, ps.text, S_SMALL, 0, 0, bx, by, bw, bh, card_scratch, sizeof card_scratch);
        for (int s = 0; s < np && k < max; s++) out[k++] = (cpage_t){0, i, s};
    }
    if (n->figure != CART_NONE && k < max) out[k++] = (cpage_t){1, 0, 0};
    return k;
}
typedef struct { int x0, y0, ymax, crop; } cpic_t;
static void card_pic_row(void *user, int y, const uint8_t *row, int bpr) {
    cpic_t *p = user; int yy = p->y0 + y;
    if (yy < 0 || yy > p->ymax || yy >= 122) return;
    for (int x = 0; x < bpr * 8; x++) {
        int xx = p->x0 + x - p->crop;
        if (xx >= 0 && xx < 250 && (row[x >> 3] & (0x80 >> (x & 7)))) fbits[yy * 32 + xx / 8] |= 0x80 >> (xx % 8);
    }
}
/* draws the card into fbits below the top line; returns true if the card was found */
static bool draw_full_card(int y, int bottom, char *foot, size_t fcap) {
    int node = card_node_of(S.card.node);
    if (node < 0 || !have_cart) return false;
    const cart_node_t *n = cart_node(&cart, (uint16_t)node);
    if (!n) return false;
    char lab[120]; cart_text(&cart, n->label, 0, lab, sizeof lab);
    int lm = cart_font_line_height(&cart, S_MED);
    line_fit(S_MED, 1, 3, y + lm - lm / 4, 244, lab);
    int by = y + lm + 1, bh = bottom - by;
    static cpage_t pg[64];
    int np = card_pages(n, 3, by, 244, bh, pg, 64);
    if (!np) { wrap(S_SMALL, 3, by, 244, bh, "(this card has no text)"); snprintf(foot, fcap, "%s", S.card.node); return true; }
    if (card_page_i >= np) card_page_i = np - 1;
    if (card_page_i < 0) card_page_i = 0;
    cpage_t *P = &pg[card_page_i];
    char cite[100] = "";
    if (P->seg == 0) {
        cart_passage_t ps = cart_node_passage(&cart, n, (unsigned)P->idx);
        cart_page(&cart, ps.text, S_SMALL, P->sub, &fb, 3, by, 244, bh, card_scratch, sizeof card_scratch);
        cart_text(&cart, ps.citation, 0, cite, sizeof cite);
    } else {
        cart_pic_t pic;
        if (!cart_pic(&cart, n->figure, &pic)) {
            cpic_t c = {0, by + (bh - (int)pic.h) / 2, bottom - 1, 0};
            if (pic.w > 250) c.crop = (pic.w - 250) / 2; else c.x0 = (250 - pic.w) / 2;
            if (c.y0 < by) c.y0 = by;
            cart_pic_decode(&pic, card_pic_row, &c);
            cart_text(&cart, pic.citation, 0, cite, sizeof cite);
        }
    }
    snprintf(foot, fcap, "%d/%d %s", card_page_i + 1, np, cite[0] ? cite : "");
    return true;
}


static void push_screen_txt(void);
/* ---------- keyword search mode (vform_search: the SRCH chunk in the cart) ----------
 * Enter: hold TALK + WIN_L (Plus) or TALK + DOWN (plain board), say one word. A 4 x 2 grid of matching
 * cards, life cards inverted. Plus: UP/DOWN move, WIN_L/WIN_R page, TALK tap opens, hold TALK = new
 * word, BACK = card -> grid -> leave. Plain: DOWN = next tile, BACK tap = previous tile (on a card:
 * back to the grid), hold BACK = leave. Opened cards are the full cards (NKEY). */
static bool sidx_ok;
static fs_index_t sidx;
static fs_hit_t shits[FS_MAX_HITS];
static int snhits, ssel;
static char squery[100];
static screen_t s_saved;                  /* the screen to return to when search mode ends */
static void search_run(const char *q) {
    snprintf(squery, sizeof squery, "%s", q);
    snhits = sidx_ok ? fs_search(&sidx, q, shits) : 0;
    ssel = 0; s_card = false;
    char t[160]; snprintf(t, sizeof t, "LOG search '%s': %d hits", q, snhits); send_line(t);
}
static void draw_grid(int y, int bottom) {
    char h[140];
    int np = (snhits + FS_PAGE - 1) / FS_PAGE, pg = ssel / FS_PAGE;
    if (!sidx_ok) { wrap(S_MED, 3, y + 4, 244, bottom - y - 4, "No search index in the library (SRCH chunk)."); return; }
    if (!squery[0]) { wrap(S_MED, 3, y + 4, 244, bottom - y - 4, "Search: hold TALK and say one word."); return; }
    if (!snhits) { snprintf(h, sizeof h, "No cards for '%s'. Hold TALK and say another word.", squery); wrap(S_MED, 3, y + 4, 244, bottom - y - 4, h); return; }
    snprintf(h, sizeof h, "'%s'  %d/%d  (%d cards)", squery, pg + 1, np, snhits);
    int ls = cart_font_line_height(&cart, S_SMALL);
    line_fit(S_SMALL, 1, 3, y + ls - ls / 4, 244, h);
    int gy = y + ls + 1, cw = 62, ch = (bottom - gy) / 2;
    for (int i = 0; i < FS_PAGE; i++) {
        int k = pg * FS_PAGE + i;
        if (k >= snhits) break;
        int x0 = (i % 4) * cw, y0 = gy + (i / 4) * ch;
        const char *lab = strstr(shits[k].title, " > "); lab = lab ? lab + 3 : shits[k].title;
        int lh = ls, ty = y0 + 2;                     /* label, word-wrapped into the tile */
        char line[64]; size_t at = 0, L = strlen(lab);
        for (int row = 0; row * lh + lh <= ch - 3 && at < L; row++) {
            size_t n = 0, brk = 0;
            while (at + n < L && n < sizeof line - 1) {
                memcpy(line, lab + at, n + 1); line[n + 1] = 0;
                if (cart_draw_str(&cart, S_SMALL, 0, 0, 0, 0, line) > cw - 6) break;
                n++; if (at + n < L && lab[at + n] == ' ') brk = n;
            }
            if (at + n < L && brk) n = brk;
            if (!n) n = 1;
            memcpy(line, lab + at, n); line[n] = 0;
            cart_draw_str(&cart, S_SMALL, k == ssel, &fb, x0 + 3, ty + lh - lh / 4, line);
            ty += lh; at += n; while (at < L && lab[at] == ' ') at++;
        }
        if (shits[k].life)                            /* life card: white on black */
            for (int yy = y0 + 1; yy < y0 + ch - 1; yy++) for (int xx = x0 + 1; xx < x0 + cw - 1; xx++) fbits[yy * 32 + xx / 8] ^= 0x80 >> (xx % 8);
        box(x0, y0, x0 + cw - 1, y0 + ch - 1);
        if (k == ssel) {                               /* highlight: a second frame, XOR so it shows on black too */
            for (int xx = x0 + 2; xx < x0 + cw - 2; xx++) { fbits[(y0 + 2) * 32 + xx / 8] ^= 0x80 >> (xx % 8); fbits[(y0 + ch - 3) * 32 + xx / 8] ^= 0x80 >> (xx % 8); }
            for (int yy = y0 + 3; yy < y0 + ch - 3; yy++) { fbits[yy * 32 + (x0 + 2) / 8] ^= 0x80 >> ((x0 + 2) % 8); fbits[yy * 32 + (x0 + cw - 3) / 8] ^= 0x80 >> ((x0 + cw - 3) % 8); }
        }
    }
}
static char card_foot[140];
static void draw(void) {
    card_foot[0] = 0;
    memset(fbits, 0, sizeof fbits);
    int ls = have_cart ? cart_font_line_height(&cart, S_SMALL) : 12;
    int lm = have_cart ? cart_font_line_height(&cart, S_MED) : 16;
    char top[160];
    const char *kn[] = { "START", "FORM", "CARD", "SKIP", "AGAIN" };
    char tn[16] = ""; if (S.test) snprintf(tn, sizeof tn, "TEST %d  ", S.test);
    if (search_mode && !s_card) snprintf(top, sizeof top, "%sSEARCH%s%s", tn, squery[0] ? "  heard: " : "", squery);
    else if (S.heard[0]) snprintf(top, sizeof top, "%s%s  heard: %s", tn, kn[S.kind], S.heard);
    else snprintf(top, sizeof top, "%s%s%s", tn, kn[S.kind], host_seen ? "" : "  (PC not connected)");
    line_fit(S_SMALL, 1, 3, ls - 2, rec_dot ? 228 : 244, top);
    if (rec_dot) for (int y = 2; y < 13; y++) for (int x = 237; x < 248; x++) fbits[y * 32 + x / 8] |= 0x80 >> (x % 8);
    hline(ls + 1);
    int y = ls + 3, bottom = 122 - ls - 2;
    if (search_mode && !s_card) {
        draw_grid(y, bottom);
    } else if (S.kind == K_FORM && S.nf) {
        field_t *f = &S.f[cur_f];
        char q[140]; snprintf(q, sizeof q, "%s", f->key[0] == '@' ? "Which is it?" : f->q);
        char pos[16]; snprintf(pos, sizeof pos, "%d/%d", cur_f + 1, S.nf);
        line_fit(S_MED, 1, 3, y + lm - lm / 4, 244 - text_w(S_SMALL, pos) - 6, q);
        line_fit(S_SMALL, 0, 247 - text_w(S_SMALL, pos), y + lm - lm / 4, 40, pos);
        y += lm + 1;
        int rows = (bottom - y) / ls; if (rows < 1) rows = 1;
        int first = cur_c - rows / 2; if (first > f->nch - rows) first = f->nch - rows; if (first < 0) first = 0;
        for (int i = first; i < f->nch && i < first + rows; i++) {
            char t[64]; snprintf(t, sizeof t, "%s %d %s", i == cur_c ? ">" : " ", i + 1, f->ch[i].label);
            line_fit(S_SMALL, i == cur_c, 3, y + ls - ls / 4, 240, t);
            if (i == cur_c) box(1, y, 248, y + ls);
            y += ls;
        }
    } else if (S.kind == K_CARD && draw_full_card(y, bottom, card_foot, sizeof card_foot)) {
        /* drawn: the real card (text pages, figure); its footer carries page and source */
    } else if (S.kind == K_CARD) {
        y = wrap(S_BIG, 3, y, 244, bottom - y - ls * 2, S.card.title);
        y = wrap(S_SMALL, 3, y, 244, bottom - y, S.cites[0] ? S.cites : "no cites");
        line_fit(S_SMALL, 0, 3, bottom, 244, S.card.node);
    } else if (S.kind == K_SKIP && S.ncards) {
        line_fit(S_SMALL, 1, 3, y + ls - ls / 4, 244, "Likely cards (TALK tap opens):"); y += ls;
        int rows = (bottom - y) / ls, first = cur_card - rows + 1; if (first < 0) first = 0;
        for (int i = first; i < S.ncards && i < first + rows; i++) {
            char t[140]; snprintf(t, sizeof t, "%s %d %s", i == cur_card ? ">" : " ", i + 1, S.cards[i].title);
            line_fit(S_SMALL, i == cur_card, 3, y + ls - ls / 4, 240, t);
            if (i == cur_card) box(1, y, 248, y + ls);
            y += ls;
        }
    } else {
        const char *m = S.msg[0] ? S.msg : S.kind == K_AGAIN ? "Didn't catch that. Hold TALK and say it again."
                      : "Hold TALK and say what happened.";
        wrap(S_MED, 3, y + 4, 244, bottom - y - 4, m);
    }
    hline(122 - ls - 1);
    const char *hint = status[0] ? status
        : search_mode && !s_card ? (BTN[B_UP] != PIN_NONE ? "UP/DN move  WIN page  TALK open/hold:word  BACK out"
                                                         : "DN next  BACK prev/hold:out  TALK open/hold:word")
        : S.kind == K_CARD && card_foot[0] ? card_foot
        : S.kind == K_FORM && S.msg[0] ? S.msg                     /* the PC's line: "say the answer..." */
        : S.kind == K_FORM ? "Hold TALK: say answers. Tap TALK: pick. DOWN: next"
        : S.kind == K_SKIP ? "TALK tap open  DOWN/BACK move  BACK hold new"
        : S.kind == K_CARD ? "Done. Hold BACK 1 sec for the next test"
        : "Hold TALK to talk   Hold BACK 1 sec: next test";
    line_fit(S_SMALL, 0, 3, 121 - ls / 4, 244, hint);
    push_screen_txt();
    xSemaphoreTake(lock, portMAX_DELAY); memcpy(fb_out, fbits, sizeof fb_out); fb_dirty = true; xSemaphoreGive(lock);
    if (HAVE_EPD) epd_show(fbits, ++screens % 10 == 0 ? EPD_CLEAN : EPD_PARTIAL);
}

/* what the screen shows, as one LOG line (for the bridge's log and for bench screenshots) */
static void push_screen_txt(void) {
    char t[256];
    if (S.kind == K_FORM && S.nf) snprintf(t, sizeof t, "LOG SCREEN seq=%d form field=%s choice=%s%s", S.seq, S.f[cur_f].key,
                                           S.f[cur_f].ch[cur_c].value, status[0] ? " status" : "");
    else snprintf(t, sizeof t, "LOG SCREEN seq=%d kind=%d %s", S.seq, S.kind, status);
    send_line(t);
}

/* ---------- picks ---------- */
static void json_str(char *out, size_t cap, const char *s) {
    size_t o = 0;
    for (; *s && o + 3 < cap; s++) { char c = *s; if (c == '"' || c == '\\') out[o++] = '\\'; out[o++] = (c >= 32 && c < 127) ? c : ' '; }
    out[o] = 0;
}
static void pick_choice(void) {
    field_t *f = &S.f[cur_f]; char k[96], v[96], l[96], b[256];
    json_str(k, sizeof k, f->key); json_str(v, sizeof v, f->ch[cur_c].value); json_str(l, sizeof l, f->ch[cur_c].label);
    if (!f->ch[cur_c].value[0] && f->ch[cur_c].node[0]) {   /* plain tile: value null + its target card */
        char n[96]; json_str(n, sizeof n, f->ch[cur_c].node);
        snprintf(b, sizeof b, "PICK {\"seq\":%d,\"field\":\"%s\",\"value\":null,\"node\":\"%s\",\"label\":\"%s\"}", S.seq, k, n, l);
    } else
        snprintf(b, sizeof b, "PICK {\"seq\":%d,\"field\":\"%s\",\"value\":\"%s\",\"label\":\"%s\"}", S.seq, k, v, l);
    send_line(b);                                          /* board brain: still logged on the PC */
#if CONFIG_TETHER_BOARD_BRAIN
    bool plain = !f->ch[cur_c].value[0] && f->ch[cur_c].node[0];
    brain_post(BR_PICK, f->key, plain ? NULL : f->ch[cur_c].value, plain ? f->ch[cur_c].node : NULL);
#endif
}
static void pick_action(const char *a, const char *node) {
    char n[96] = "", b[256];
    if (node) json_str(n, sizeof n, node);
    snprintf(b, sizeof b, "PICK {\"seq\":%d,\"action\":\"%s\"%s%s%s}", S.seq, a, node ? ",\"node\":\"" : "", n, node ? "\"" : "");
    send_line(b);
#if CONFIG_TETHER_BOARD_BRAIN
    brain_post(!strcmp(a, "skip") ? BR_SKIP : !strcmp(a, "open") ? BR_OPEN : BR_NEW, node, "", NULL);
#endif
}
static void move(int d) {
    if (S.kind == K_FORM && S.nf) {
        cur_c += d;
        if (cur_c >= S.f[cur_f].nch) { cur_f = (cur_f + 1) % S.nf; cur_c = 0; }
        if (cur_c < 0) { cur_f = (cur_f + S.nf - 1) % S.nf; cur_c = S.f[cur_f].nch - 1; }
    } else if (S.kind == K_SKIP && S.ncards) cur_card = (cur_card + d + S.ncards) % S.ncards;
    else if (S.kind == K_CARD) card_page_i += d;     /* full card: DOWN / BACK tap turn pages */
}

/* ---------- buttons ---------- */
static bool down(int b) { return BTN[b] != PIN_NONE && gpio_get_level(BTN[b]) == 0; }
/* wait while held; returns ms held, or stops at `cap` ms (still held) */
static int held_ms(int b, int cap) { int t = 0; while (down(b) && t < cap) { vTaskDelay(pdMS_TO_TICKS(10)); t += 10; } return t; }

void app_main(void) {
    esp_log_level_set("*", ESP_LOG_WARN);
    nvs_flash_init();
    lock = xSemaphoreCreateMutex();
    outq = xQueueCreate(16, 256);
    usb_serial_jtag_driver_config_t uc = { .rx_buffer_size = 8192, .tx_buffer_size = 16384 };
    usb_serial_jtag_driver_install(&uc);
    clip_cap = (size_t)SR * (MAX_TAKE_S + 1);
    clip = heap_caps_malloc(clip_cap * 2, MALLOC_CAP_SPIRAM);
    const esp_partition_t *p = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, "library");
    const void *rom; esp_partition_mmap_handle_t mh;
    if (p && esp_partition_mmap(p, 0, p->size, ESP_PARTITION_MMAP_DATA, &rom, &mh) == ESP_OK && (cart_rom = rom, cart_len = p->size, 1))
        have_cart = cart_open(&cart, rom, p->size, 0) == 0 && cart_font_sizes(&cart) > 0;
    if (have_cart) {                                      /* full cards for voice results (NKEY chunk) */
        const uint8_t *b = cart_rom; uint16_t nc = (uint16_t)(b[6] | b[7] << 8);
        for (uint16_t i = 0; i < nc; i++) { const uint8_t *d = b + 28 + 12 * i; if (!memcmp(d, "NKEY", 4)) nkey = b + rd32le(d + 4); }
        if (nkey && memcmp(nkey, "NK01", 4)) nkey = NULL;
        for (uint16_t i = 0; i < nc; i++) {                  /* keyword search index (SRCH chunk) */
            const uint8_t *d = b + 28 + 12 * i;
            if (!memcmp(d, "SRCH", 4)) sidx_ok = fs_open(&sidx, b + rd32le(d + 4), rd32le(d + 8));
        }
    }
    if (!have_cart) ESP_LOGE(TAG, "no library in flash (fonts): flash the main firmware once first");
    else if (cart_font_sizes(&cart) < 3) { S_MED = S_BIG = cart_font_sizes(&cart) - 1; }
    uint64_t bmask = 0;
    for (int i = 0; i < 6; i++) if (BTN[i] != PIN_NONE) bmask |= 1ULL << BTN[i];
    gpio_config_t g = { .pin_bit_mask = bmask,
                        .mode = GPIO_MODE_INPUT, .pull_up_en = 1 };
    gpio_config(&g);
    if (HAVE_EPD) { epd_init(); }
    mic_init();
    xTaskCreatePinnedToCore(usb_task, "usb", 8192, 0, 5, 0, 0);
#if CONFIG_TETHER_BOARD_BRAIN
    brainq = xQueueCreate(4, sizeof(brain_req_t));
    shown = heap_caps_malloc(16400, MALLOC_CAP_SPIRAM);
    xTaskCreatePinnedToCore(brain_task, "brain", 32768, 0, 4, 0, 1);
#endif
#if CONFIG_TETHER_STANDALONE
    asr_setup();
#endif
    S.kind = K_START;
    if (HAVE_EPD) { memset(fbits, 0, sizeof fbits); epd_show(fbits, EPD_FULL); }
    draw();

    uint32_t seen_ver = 0; bool last_host = false; int64_t wait_since = 0;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(10));
        if (seen_ver != scr_ver) {                                 /* a new screen from the PC */
            xSemaphoreTake(lock, portMAX_DELAY); seen_ver = scr_ver; S = scr_in; xSemaphoreGive(lock);
            cur_f = cur_c = cur_card = 0; card_page_i = 0; status[0] = 0; wait_since = 0;
            draw();
        }
        if (last_host != host_seen) { last_host = host_seen; draw(); }
        if (wait_since && esp_timer_get_time() - wait_since > 20000000) {
            wait_since = 0; snprintf(status, sizeof status, "No answer from the PC (is pc_bridge running?)"); draw();
        }
        if (s_words_ready) {                                       /* a search word arrived (PC or board speech) */
            xSemaphoreTake(lock, portMAX_DELAY); char q[400]; snprintf(q, sizeof q, "%s", s_words); int rj = s_reject; s_words_ready = false; xSemaphoreGive(lock);
            status[0] = 0; wait_since = 0;
            if (rj >= CONFIG_TETHER_REJECT_ANY) snprintf(status, sizeof status, "Didn't catch that. Hold TALK, say one word.");
            else if (!strcmp(q, "home")) { search_mode = false; s_card = false; S = s_saved; send_line("MODE form"); }
            else if (!strcmp(q, "back")) { if (s_card) s_card = false; else { search_mode = false; S = s_saved; send_line("MODE form"); } }
            else if (strcmp(q, "again")) search_run(q);
            draw();
            continue;
        }
        if (search_mode && !down(0)) {                             /* search mode buttons (TALK is shared below) */
            bool plus = BTN[B_UP] != PIN_NONE;
            int np = (snhits + FS_PAGE - 1) / FS_PAGE;
            if (down(B_DOWN)) { held_ms(B_DOWN, 700); if (s_card) card_page_i++; else if (snhits) ssel = (ssel + 1) % snhits; draw(); while (down(B_DOWN)) vTaskDelay(10); continue; }
            if (plus && down(B_UP)) { held_ms(B_UP, 700); if (s_card) card_page_i--; else if (snhits) ssel = (ssel + snhits - 1) % snhits; draw(); while (down(B_UP)) vTaskDelay(10); continue; }
            if (plus && down(B_WIN_R)) { held_ms(B_WIN_R, 700); if (!s_card && np) ssel = ((ssel / FS_PAGE + 1) % np) * FS_PAGE; draw(); continue; }
            if (plus && down(B_WIN_L)) { held_ms(B_WIN_L, 700); if (!s_card && np) ssel = ((ssel / FS_PAGE + np - 1) % np) * FS_PAGE; draw(); continue; }
            if (down(B_BACK)) {
                int t = held_ms(B_BACK, 700);
                if (t >= 700 || (!s_card && plus)) { search_mode = false; s_card = false; S = s_saved; send_line("MODE form"); }
                else if (s_card) { s_card = false; }
                else if (snhits) ssel = (ssel + snhits - 1) % snhits;
                draw(); while (down(B_BACK)) vTaskDelay(10);
                continue;
            }
        }
        if (down(0)) {                                             /* TALK: tap = pick, hold = speak */
            int t = held_ms(0, HOLD_MS);
            bool combo = down(BTN[B_WIN_L] != PIN_NONE ? B_WIN_L : B_DOWN);   /* TALK + WIN_L (Plus) / + DOWN (plain) */
            if (t >= HOLD_MS && combo && !search_mode) {           /* enter keyword search */
                s_saved = S; search_mode = true; s_card = false; squery[0] = 0; snhits = 0;
                send_line("MODE search");
            }
            if (t < HOLD_MS && search_mode) {                      /* search: open the highlighted card */
                if (!s_card && snhits) {
                    s_card = true; card_page_i = 0;
                    memset(&S, 0, sizeof S); S.kind = K_CARD; S.test = s_saved.test;
                    snprintf(S.card.node, sizeof S.card.node, "%s", shits[ssel].node);
                    snprintf(S.card.title, sizeof S.card.title, "%s", shits[ssel].title);
                    draw();
                }
                continue;
            }
            if (t < HOLD_MS) {
                if (S.kind == K_FORM && S.nf) { pick_choice(); snprintf(status, sizeof status, "Picked: %s ...", S.f[cur_f].ch[cur_c].label); wait_since = esp_timer_get_time(); draw(); }
                else if (S.kind == K_SKIP && S.ncards) { pick_action("open", S.cards[cur_card].node); snprintf(status, sizeof status, "Opening ..."); wait_since = esp_timer_get_time(); draw(); }
                continue;
            }
            rec_start(); rec_dot = true; snprintf(status, sizeof status, "Listening... let go when done"); draw();
            while (down(0) && clip_n < clip_cap - SR / 2) vTaskDelay(pdMS_TO_TICKS(10));
            rec_stop(); rec_dot = false;
#if CONFIG_TETHER_STANDALONE
            if (asr_take()) { if (!host_seen) take_state = 0; snprintf(status, sizeof status, "Thinking (on the board)..."); draw(); continue; }
            if (!host_seen) { take_state = 0; snprintf(status, sizeof status, "Board speech not built yet and no PC: use the buttons"); draw(); continue; }
#endif
            if (search_mode) { while (down(B_DOWN) || down(B_WIN_L)) vTaskDelay(10); }
            if (take_peak < 300) snprintf(status, sizeof status, "No sound? check mic (CLK D4, DAT D5). Sent anyway");
            else snprintf(status, sizeof status, "Thinking... (%.1f s sent)", clip_n / (float)SR);
            wait_since = esp_timer_get_time();
            draw();
        } else if (down(2)) {                                      /* DOWN: tap = next, hold = SKIP */
            int t = held_ms(2, 700);
            if (t >= 700) { pick_action("skip", NULL); snprintf(status, sizeof status, "SKIP ..."); wait_since = esp_timer_get_time(); draw(); while (down(2)) vTaskDelay(10); }
            else { move(+1); draw(); }
        } else if (down(1)) {                                      /* BACK: tap = previous, hold = new problem */
            int t = held_ms(1, 700);
            if (t >= 700) {
                pick_action("new", NULL);
                memset(&S, 0, sizeof S); S.kind = K_START; cur_f = cur_c = cur_card = 0;
                snprintf(status, sizeof status, "New problem: hold TALK and say what happened"); draw();
                while (down(1)) vTaskDelay(10);
            } else { move(-1); draw(); }
        }
    }
}
