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

#ifdef CONFIG_REC_EPD
#define HAVE_EPD 1
#else
#define HAVE_EPD 0
#endif

#define SR 16000
#define PRE_MS 500
#define HOLD_MS 300
#define MAX_TAKE_S 15
static const char *TAG = "tether";
static SemaphoreHandle_t lock;

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
    int seq, kind;
    char heard[128], msg[128];
    int nf; field_t f[6];
    int ntop; cardref_t top[3];
    cardref_t card; char cites[200];
    int ncards; cardref_t cards[6];
} screen_t;
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
}
static void usb_task(void *arg) {
    static char line[8192]; int n = 0; uint8_t in[256]; char out[256];
    for (;;) {
        while (xQueueReceive(outq, out, 0) == pdTRUE) usb_line(out);
        if (take_state == 1 && !recording) send_take();
        if (fb_dirty && host_seen) send_fb();
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

static void push_screen_txt(void);
static void draw(void) {
    memset(fbits, 0, sizeof fbits);
    int ls = have_cart ? cart_font_line_height(&cart, S_SMALL) : 12;
    int lm = have_cart ? cart_font_line_height(&cart, S_MED) : 16;
    char top[160];
    const char *kn[] = { "START", "FORM", "CARD", "SKIP", "AGAIN" };
    if (S.heard[0]) snprintf(top, sizeof top, "%s  heard: %s", kn[S.kind], S.heard);
    else snprintf(top, sizeof top, "%s%s", kn[S.kind], host_seen ? "" : "  (PC not connected)");
    line_fit(S_SMALL, 1, 3, ls - 2, rec_dot ? 228 : 244, top);
    if (rec_dot) for (int y = 2; y < 13; y++) for (int x = 237; x < 248; x++) fbits[y * 32 + x / 8] |= 0x80 >> (x % 8);
    hline(ls + 1);
    int y = ls + 3, bottom = 122 - ls - 2;
    if (S.kind == K_FORM && S.nf) {
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
        : S.kind == K_FORM && S.msg[0] ? S.msg                     /* the PC's line: "say the answer..." */
        : S.kind == K_FORM ? "Hold TALK: say answers. Tap TALK: pick. DOWN: next"
        : S.kind == K_SKIP ? "TALK tap open  DOWN/BACK move  BACK hold new"
        : S.kind == K_CARD ? "Hold TALK: new problem   BACK hold: start"
        : "Hold TALK to talk   BACK hold: start over";
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
    send_line(b);
}
static void pick_action(const char *a, const char *node) {
    char n[96] = "", b[256];
    if (node) json_str(n, sizeof n, node);
    snprintf(b, sizeof b, "PICK {\"seq\":%d,\"action\":\"%s\"%s%s%s}", S.seq, a, node ? ",\"node\":\"" : "", n, node ? "\"" : "");
    send_line(b);
}
static void move(int d) {
    if (S.kind == K_FORM && S.nf) {
        cur_c += d;
        if (cur_c >= S.f[cur_f].nch) { cur_f = (cur_f + 1) % S.nf; cur_c = 0; }
        if (cur_c < 0) { cur_f = (cur_f + S.nf - 1) % S.nf; cur_c = S.f[cur_f].nch - 1; }
    } else if (S.kind == K_SKIP && S.ncards) cur_card = (cur_card + d + S.ncards) % S.ncards;
}

/* ---------- buttons ---------- */
static const int BTN[3] = { PIN_BTN_TALK, PIN_BTN_BACK, PIN_BTN_DOWN };
static bool down(int b) { return gpio_get_level(BTN[b]) == 0; }
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
    if (p && esp_partition_mmap(p, 0, p->size, ESP_PARTITION_MMAP_DATA, &rom, &mh) == ESP_OK)
        have_cart = cart_open(&cart, rom, p->size, 0) == 0 && cart_font_sizes(&cart) > 0;
    if (!have_cart) ESP_LOGE(TAG, "no library in flash (fonts): flash the main firmware once first");
    else if (cart_font_sizes(&cart) < 3) { S_MED = S_BIG = cart_font_sizes(&cart) - 1; }
    gpio_config_t g = { .pin_bit_mask = (1ULL << PIN_BTN_TALK) | (1ULL << PIN_BTN_BACK) | (1ULL << PIN_BTN_DOWN),
                        .mode = GPIO_MODE_INPUT, .pull_up_en = 1 };
    gpio_config(&g);
    if (HAVE_EPD) { epd_init(); }
    mic_init();
    xTaskCreatePinnedToCore(usb_task, "usb", 8192, 0, 5, 0, 0);
    S.kind = K_START;
    if (HAVE_EPD) { memset(fbits, 0, sizeof fbits); epd_show(fbits, EPD_FULL); }
    draw();

    uint32_t seen_ver = 0; bool last_host = false; int64_t wait_since = 0;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(10));
        if (seen_ver != scr_ver) {                                 /* a new screen from the PC */
            xSemaphoreTake(lock, portMAX_DELAY); seen_ver = scr_ver; S = scr_in; xSemaphoreGive(lock);
            cur_f = cur_c = cur_card = 0; status[0] = 0; wait_since = 0;
            draw();
        }
        if (last_host != host_seen) { last_host = host_seen; draw(); }
        if (wait_since && esp_timer_get_time() - wait_since > 20000000) {
            wait_since = 0; snprintf(status, sizeof status, "No answer from the PC (is pc_bridge running?)"); draw();
        }
        if (down(0)) {                                             /* TALK: tap = pick, hold = speak */
            int t = held_ms(0, HOLD_MS);
            if (t < HOLD_MS) {
                if (S.kind == K_FORM && S.nf) { pick_choice(); snprintf(status, sizeof status, "Picked: %s ...", S.f[cur_f].ch[cur_c].label); wait_since = esp_timer_get_time(); draw(); }
                else if (S.kind == K_SKIP && S.ncards) { pick_action("open", S.cards[cur_card].node); snprintf(status, sizeof status, "Opening ..."); wait_since = esp_timer_get_time(); draw(); }
                continue;
            }
            rec_start(); rec_dot = true; snprintf(status, sizeof status, "Listening... let go when done"); draw();
            while (down(0) && clip_n < clip_cap - SR / 2) vTaskDelay(pdMS_TO_TICKS(10));
            rec_stop(); rec_dot = false;
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
