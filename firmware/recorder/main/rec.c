/* rec.c - FASR recorder: collect labelled speech through the device's own PDM mic.
 *
 * The screen (or, with no e-ink wired, the PC running `fasr devrec`) shows a sentence. Hold TALK, read it, let go. The clip (16 kHz mono 16-bit,
 * with 300 ms of audio from before the press) is kept in PSRAM until the PC copies it:
 *     python -m fasr devrec            (pipeline/speech; writes WAVs + manifests)
 * Clips not yet copied are lost if power is cut; the screen shows how many are waiting.
 *
 * Buttons (all three bench buttons; on the Plus the same three are used):
 *   TALK  hold = record, release = save and go to the next sentence
 *   BACK  short = redo the previous sentence (the old clip is replaced);  long = switch speaker ME / GUEST
 *         (each speaker keeps their own place in every mode; clips carry "speaker" so a guest's EVAL
 *          clips become a held-out-speaker test set)
 *   DOWN  short = skip this sentence;  long = next mode
 * Modes: TRAIN (sentences for training), EVAL (held-out sentences, scoring only),
 *        FREE (say anything about an injury; Whisper labels it later),
 *        NOISE (10 s of background: wind, stream, rain, fan, rustling clothes),
 *        PAIRS (sentences built around words the model confuses: bleeding/breathing, stop/drop...),
 *        ANSWERS (a device question and a short answer to say, like the intake pages will ask).
 *
 * USB protocol (text lines, so logs can't corrupt it):
 *   PC -> device:  "HELLO", "SYNC" (send clips not sent yet), "ACK <id>", "RESEND" (after a bad transfer)
 *   device -> PC:  "FASR-REC 1 <pending>", "SCREEN <top> | <prompt> | <status>" (what the e-ink shows)
 *                  "TAKE {json}"  then "D <base64>" lines  then "END <crc32 hex>"
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "board.h"
#include "cart.h"
#include "epd.h"
#include "driver/gpio.h"
#include "driver/i2s_pdm.h"
#include "driver/usb_serial_jtag.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_partition.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"
#include "nvs.h"
#include "nvs_flash.h"

#ifdef CONFIG_REC_EPD
#define REC_EPD 1
#else
#define REC_EPD 0   /* no panel: the PC shows the prompts */
#endif

#define SR 16000
#define PRE_MS 300
#define MAX_TAKE_S 15
#define NOISE_S 10
#define MAX_TAKES 512
static const char *TAG = "rec";

extern const char prompts_start[] asm("_binary_prompts_txt_start");
extern const char prompts_end[] asm("_binary_prompts_txt_end");

/* ---------- prompts: lines "T|text" (train), "E|text" (eval), "P|text" (confusable pairs),
 * "A|question|answer" (answers: the screen shows both, the label is the answer) ----------
 * New modes go at the END of the enum so the positions saved in NVS keep their meaning. */
enum { M_TRAIN, M_EVAL, M_FREE, M_NOISE, M_PAIRS, M_ANSWERS, M_COUNT };
static const char *MODE_NAME[M_COUNT] = { "TRAIN", "EVAL", "FREE", "NOISE", "PAIRS", "ANSWERS" };
static const char *MODE_KEY[M_COUNT] = { "train", "eval", "free", "noise", "pairs", "answers" };
static int kind_of(int m) { return m == M_TRAIN ? 0 : m == M_EVAL ? 1 : m == M_PAIRS ? 2 : m == M_ANSWERS ? 3 : -1; }
#define NKIND 4
typedef struct { const char *s; uint16_t len; } line_t;
static line_t *lines[NKIND]; static int nlines[NKIND];

/* the words to say: for "question|answer" lines, the part after the last '|' */
static line_t said(line_t L) {
    for (int i = L.len - 1; i >= 0; i--) if (L.s[i] == '|') { line_t a = { L.s + i + 1, (uint16_t)(L.len - i - 1) }; return a; }
    return L;
}

static void load_prompts(void) {
    for (int k = 0; k < NKIND; k++) lines[k] = heap_caps_calloc(4000, sizeof(line_t), MALLOC_CAP_SPIRAM);
    for (const char *p = prompts_start; p < prompts_end; ) {
        const char *e = memchr(p, '\n', prompts_end - p); if (!e) e = prompts_end;
        if (e - p > 2 && p[1] == '|') {
            int k = p[0] == 'E' ? 1 : p[0] == 'P' ? 2 : p[0] == 'A' ? 3 : 0;
            if (nlines[k] < 4000) { lines[k][nlines[k]].s = p + 2; lines[k][nlines[k]].len = (uint16_t)(e - p - 2 - (e[-1] == '\r')); nlines[k]++; }
        }
        p = e + 1;
    }
}

/* ---------- state kept in NVS so a restart carries on ---------- */
static const char *SPK_NAME[2] = { "ME", "GUEST" }, *SPK_KEY[2] = { "me", "guest" };
static int mode, spk, idxs[2][M_COUNT];
#define idx (idxs[spk])
static uint32_t next_id;
static void nvs_save(void) {
    nvs_handle_t h; if (nvs_open("fasrrec", NVS_READWRITE, &h)) return;
    nvs_set_u8(h, "mode", (uint8_t)mode); nvs_set_u8(h, "spk", (uint8_t)spk);
    for (int m = 0; m < M_COUNT; m++) { char k[8];
        snprintf(k, sizeof k, "i%d", m); nvs_set_i32(h, k, idxs[0][m]);      /* ME keeps the old keys */
        snprintf(k, sizeof k, "g%d", m); nvs_set_i32(h, k, idxs[1][m]); }
    nvs_set_u32(h, "id", next_id);
    nvs_commit(h); nvs_close(h);
}
static void nvs_load(void) {
    nvs_handle_t h; if (nvs_open("fasrrec", NVS_READONLY, &h)) return;
    uint8_t m = 0; nvs_get_u8(h, "mode", &m); mode = m % M_COUNT;
    uint8_t sp = 0; nvs_get_u8(h, "spk", &sp); spk = sp & 1;
    for (int i = 0; i < M_COUNT; i++) { char k[8]; int32_t v;
        snprintf(k, sizeof k, "i%d", i); v = 0; nvs_get_i32(h, k, &v); idxs[0][i] = v;
        snprintf(k, sizeof k, "g%d", i); v = 0; nvs_get_i32(h, k, &v); idxs[1][i] = v; }
    nvs_get_u32(h, "id", &next_id);
    nvs_close(h);
}

/* ---------- take store in PSRAM (append; reset when everything is copied) ---------- */
typedef struct { uint32_t id, redo_of; uint8_t mode, spk, sent, acked; int32_t prompt; uint32_t off, n; int16_t peak; uint16_t clip_pm; uint16_t range; } take_t;
static int16_t *store; static size_t store_cap, store_used;     /* in samples */
static take_t takes[MAX_TAKES]; static int ntakes;
static SemaphoreHandle_t lock;

static int pending(void) { int n = 0; for (int i = 0; i < ntakes; i++) n += !takes[i].acked; return n; }
static volatile bool usb_sending;     /* the USB task is reading clips out of the store */
static void compact_if_done(void) { if (ntakes && !pending()) { ntakes = 0; store_used = 0; } }
/* Free the space of copied clips by sliding the not-yet-copied ones down. Needed because
 * someone recording steadily almost never has zero clips in flight. Called with the lock held,
 * never while the USB task is sending (it holds indices and offsets into the store). */
static void compact_store(void) {
    if (usb_sending) return;
    size_t w = 0; int j = 0;
    for (int i = 0; i < ntakes; i++) {
        take_t t = takes[i];
        if (t.acked) continue;
        if (t.off != w) memmove(store + w, store + t.off, (size_t)t.n * 2);
        t.off = w; w += t.n; takes[j++] = t;
    }
    ntakes = j; store_used = w;
}

/* ---------- microphone: always running into a pre-roll ring ---------- */
static i2s_chan_handle_t rx;
static int16_t pre[SR * PRE_MS / 1000]; static size_t pre_pos;
static volatile bool recording; static volatile size_t rec_n; static size_t rec_off, rec_max;

/* The mic talks on one clock edge (set by SEL); which edge the S3 calls "left" has varied between
 * chips and IDF versions. So read both slots and keep whichever one is moving: the idle slot reads
 * a stuck constant. act[] is a slow average of sample-to-sample change per slot. */
static uint32_t act[2]; static volatile int mic_slot;
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
            size_t room = rec_max - rec_n, k = n < room ? n : room;
            memcpy(store + rec_off + rec_n, buf, k * 2); rec_n += k;
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

/* start: copy the pre-roll in front; returns false if the store is full */
static bool rec_start(int max_s) {
    size_t need = (size_t)SR * (max_s + 1);
    xSemaphoreTake(lock, portMAX_DELAY);
    compact_if_done();
    if (ntakes >= MAX_TAKES || store_used + need > store_cap) compact_store();
    bool ok = ntakes < MAX_TAKES && store_used + need <= store_cap;
    if (ok) {
        rec_off = store_used;
        size_t np = sizeof pre / 2, p = pre_pos;
        for (size_t i = 0; i < np; i++) store[rec_off + i] = pre[(p + i) % np];
        rec_n = np; rec_max = need; recording = true;
    }
    xSemaphoreGive(lock);
    return ok;
}

static take_t *rec_stop(int32_t prompt, uint32_t redo_of) {
    vTaskDelay(pdMS_TO_TICKS(250));            /* keep the word's tail */
    recording = false;
    vTaskDelay(pdMS_TO_TICKS(20));
    xSemaphoreTake(lock, portMAX_DELAY);
    take_t *t = &takes[ntakes++];
    memset(t, 0, sizeof *t);
    t->id = next_id++; t->redo_of = redo_of; t->mode = (uint8_t)mode; t->spk = (uint8_t)spk; t->prompt = prompt; t->off = rec_off; t->n = rec_n;
    int peak = 0, clip = 0, lo = 32767, hi = -32768;
    for (uint32_t i = 0; i < t->n; i++) {
        int s0 = store[t->off + i], v = abs(s0);
        if (v > peak) { peak = v; }
        clip += v > 32000;
        if (s0 < lo) { lo = s0; }
        if (s0 > hi) { hi = s0; }
    }
    t->range = (uint16_t)(t->n ? (hi - lo > 65535 ? 65535 : hi - lo) : 0);   /* ~0 = stuck data line, no sound */
    t->peak = (int16_t)(peak > 32767 ? 32767 : peak); t->clip_pm = (uint16_t)(1000LL * clip / (t->n ? t->n : 1));
    store_used += t->n;
    xSemaphoreGive(lock);
    return t;
}

/* ---------- USB link ---------- */
static uint32_t crc32(const uint8_t *p, size_t n, uint32_t c) {
    c = ~c;
    while (n--) { c ^= *p++; for (int k = 0; k < 8; k++) c = c >> 1 ^ (0xEDB88320u & -(c & 1)); }
    return ~c;
}
static void usb_write(const char *s, size_t n) {
    while (n) { int w = usb_serial_jtag_write_bytes(s, n, pdMS_TO_TICKS(500)); if (w <= 0) return; s += w; n -= w; }
}
static void usb_line(const char *s) { usb_write(s, strlen(s)); usb_write("\n", 1); }

static void json_escape(char *out, size_t cap, const char *s, size_t len) {
    size_t o = 0;
    for (size_t i = 0; i < len && o + 3 < cap; i++) { char c = s[i]; if (c == '"' || c == '\\') out[o++] = '\\'; out[o++] = (c >= 32 && c < 127) ? c : ' '; }
    out[o] = 0;
}

static void send_take(take_t *t) {
    static char hdr[512], txt[320];
    txt[0] = 0;
    if (kind_of(t->mode) >= 0 && t->prompt >= 0) { line_t L = said(lines[kind_of(t->mode)][t->prompt]); json_escape(txt, sizeof txt, L.s, L.len); }
    snprintf(hdr, sizeof hdr, "TAKE {\"id\": %lu, \"mode\": \"%s\", \"prompt\": %ld, \"text\": \"%s\", \"samples\": %lu, "
             "\"sr\": %d, \"peak\": %d, \"clip_per_mille\": %u, \"redo_of\": %ld, \"board\": \"%s\", \"speaker\": \"%s\"}",
             (unsigned long)t->id, MODE_KEY[t->mode], (long)t->prompt, txt, (unsigned long)t->n, SR, t->peak, t->clip_pm,
             t->redo_of ? (long)t->redo_of : -1L, BOARD_NAME, SPK_KEY[t->spk & 1]);
    usb_line(hdr);
    const uint8_t *p = (const uint8_t *)(store + t->off);
    size_t left = t->n * 2; uint32_t c = 0;
    static unsigned char b64[4200]; size_t olen;
    while (left) {
        size_t k = left > 3072 ? 3072 : left;
        mbedtls_base64_encode(b64 + 2, sizeof b64 - 3, &olen, p, k);
        b64[0] = 'D'; b64[1] = ' '; b64[olen + 2] = '\n';
        usb_write((const char *)b64, olen + 3);
        c = crc32(p, k, c); p += k; left -= k;
    }
    char end[24]; snprintf(end, sizeof end, "END %08lx", (unsigned long)c); usb_line(end);
    t->sent = 1;
}

/* The current screen as one text line, sent to the PC by the USB task (the only USB writer),
 * so `fasr devrec` can show the prompt even when no e-ink panel is wired. */
static char screen_txt[400];
static volatile uint32_t screen_ver;

static volatile int64_t last_host_us;
static void usb_task(void *arg) {
    char line[48]; int n = 0; uint8_t ch; uint32_t sent_ver = 0;
    static char scr[400];
    for (;;) {
        if (sent_ver != screen_ver) {
            xSemaphoreTake(lock, portMAX_DELAY); sent_ver = screen_ver; strcpy(scr, screen_txt); xSemaphoreGive(lock);
            usb_line(scr);
        }
        if (usb_serial_jtag_read_bytes(&ch, 1, pdMS_TO_TICKS(50)) != 1) continue;
        if (ch != '\n' && ch != '\r') { if (n < (int)sizeof line - 1) line[n++] = (char)ch; continue; }
        line[n] = 0; n = 0;
        if (!line[0]) continue;
        last_host_us = esp_timer_get_time();
        if (!strcmp(line, "HELLO")) { char s[40]; snprintf(s, sizeof s, "FASR-REC 1 %d", pending()); usb_line(s); }
        else if (!strcmp(line, "SYNC")) {
            /* snapshot under the lock, send without it so recording never waits on USB.
             * Clips are only moved (compact_store) while no send is running. */
            static int todo[MAX_TAKES]; int k = 0;
            xSemaphoreTake(lock, portMAX_DELAY);
            for (int i = 0; i < ntakes; i++) if (!takes[i].acked && !takes[i].sent) todo[k++] = i;
            usb_sending = k > 0;
            xSemaphoreGive(lock);
            for (int i = 0; i < k; i++) send_take(&takes[todo[i]]);
            usb_sending = false;
            usb_line("SYNCED");
        }
        else if (!strcmp(line, "RESEND")) { xSemaphoreTake(lock, portMAX_DELAY); for (int i = 0; i < ntakes; i++) takes[i].sent = 0; xSemaphoreGive(lock); }
        else if (!strncmp(line, "ACK ", 4)) {
            uint32_t id = strtoul(line + 4, 0, 10);
            xSemaphoreTake(lock, portMAX_DELAY);
            for (int i = 0; i < ntakes; i++) if (takes[i].id == id) takes[i].acked = 1;
            xSemaphoreGive(lock);
        }
    }
}

/* ---------- screen ---------- */
static cart_t cart; static bool have_cart;
static uint8_t fbits[32 * 122];
static cart_fb_t fb = { fbits, 32, 250, 122 };
static int screens;

static void wrap(unsigned style, int x, int y, int w, int h, const char *s, size_t len) {
    int lh = cart_font_line_height(&cart, style), cy = y; char line[96]; size_t i = 0;
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
}

static void draw(const char *status, bool rec) {
    memset(fbits, 0, sizeof fbits);
    char top[80];
    int kd = kind_of(mode), total = kd >= 0 ? nlines[kd] : 0;
    if (kd >= 0) snprintf(top, sizeof top, "%s %s %d/%d   unsent %d", SPK_NAME[spk], MODE_NAME[mode], idx[mode] + 1, total, pending());
    else snprintf(top, sizeof top, "%s %s #%d   unsent %d", SPK_NAME[spk], MODE_NAME[mode], idx[mode] + 1, pending());
    cart_draw_str(&cart, 0, 1, &fb, 4, 12, top);
    if (rec) for (int y = 2; y < 14; y++) for (int x = 236; x < 248; x++) fbits[y * 32 + x / 8] |= 0x80 >> (x % 8);  /* REC dot */
    const char *body; size_t blen; char tmp[200];
    if (kd == 3 && total && said(lines[kd][idx[mode] % total]).s != lines[kd][idx[mode] % total].s) {  /* ANSWERS: "Q?  Say: answer" */
        line_t L = lines[kd][idx[mode] % total], A = said(L);
        snprintf(tmp, sizeof tmp, "%.*s  Say: %.*s", (int)(A.s - L.s - 1), L.s, (int)A.len, A.s); body = tmp; blen = strlen(tmp);
    }
    else if (kd >= 0 && total) { line_t L = lines[kd][idx[mode] % total]; body = L.s; blen = L.len; }
    else if (mode == M_FREE) { body = "Say what happened, like you would on a trail. Any injury, any words."; blen = strlen(body); }
    else { snprintf(tmp, sizeof tmp, "Hold TALK %d s for background only: wind, stream, rain, fan, clothes. Don't talk.", NOISE_S); body = tmp; blen = strlen(tmp); }
    const char *st = status ? status : "Hold TALK to read  BACK redo/hold: who  DOWN skip/hold: mode";
    xSemaphoreTake(lock, portMAX_DELAY);
    snprintf(screen_txt, sizeof screen_txt, "SCREEN %s%s | %.*s | %s", rec ? "[REC] " : "", top, (int)blen, body, st);
    for (char *c = screen_txt; *c; c++) if (*c == '\n' || *c == '\r') *c = ' ';
    screen_ver++;
    xSemaphoreGive(lock);
    if (!REC_EPD) return;
    if (!have_cart) { epd_show(fbits, true); return; }
    unsigned big = cart_font_sizes(&cart) - 1;
    wrap(big, 4, 18, 242, 82, body, blen);
    cart_draw_str(&cart, 0, 0, &fb, 4, 118, st);
    epd_show(fbits, ++screens % 8 == 0);
}

/* ---------- buttons (polled; three of them) ---------- */
static const int BTN[3] = { PIN_BTN_TALK, PIN_BTN_BACK, PIN_BTN_DOWN };
static bool down(int b) { return gpio_get_level(BTN[b]) == 0; }

void app_main(void) {
    esp_log_level_set("*", ESP_LOG_WARN);
    nvs_flash_init();
    lock = xSemaphoreCreateMutex();
    usb_serial_jtag_driver_config_t uc = { .rx_buffer_size = 1024, .tx_buffer_size = 16384 };
    usb_serial_jtag_driver_install(&uc);
    load_prompts(); nvs_load();
    store_cap = (size_t)CONFIG_REC_BUFFER_KB * 1024 / 2;
    store = heap_caps_malloc(store_cap * 2, MALLOC_CAP_SPIRAM);
    if (!store) { store_cap = 1024 * 1024; store = heap_caps_malloc(store_cap * 2, MALLOC_CAP_SPIRAM); }
    const esp_partition_t *p = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, "library");
    const void *rom; esp_partition_mmap_handle_t mh;
    if (p && esp_partition_mmap(p, 0, p->size, ESP_PARTITION_MMAP_DATA, &rom, &mh) == ESP_OK)
        have_cart = cart_open(&cart, rom, p->size, 0) == 0 && cart_font_sizes(&cart) > 0;
    if (!have_cart) ESP_LOGE(TAG, "no library in flash (fonts): flash the main firmware once first");
    gpio_config_t g = { .pin_bit_mask = (1ULL << PIN_BTN_TALK) | (1ULL << PIN_BTN_BACK) | (1ULL << PIN_BTN_DOWN),
                        .mode = GPIO_MODE_INPUT, .pull_up_en = 1 };
    gpio_config(&g);
    if (REC_EPD) epd_init();
    mic_init();
    xTaskCreatePinnedToCore(usb_task, "usb", 6144, 0, 5, 0, 0);
    draw(0, false);

    uint32_t last_id = 0; int32_t last_prompt = -1; int last_mode = -1;
    uint32_t redo_of = 0;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(10));
        if (down(0)) {                                             /* TALK: record while held */
            int max_s = mode == M_NOISE ? NOISE_S : MAX_TAKE_S;
            if (!rec_start(max_s)) { draw("Memory full: plug in USB and run  fasr devrec", false); while (down(0)) vTaskDelay(10); continue; }
            draw("Recording... let go when done", true);
            int64_t t0 = esp_timer_get_time();
            while (down(0) && rec_n < (size_t)SR * max_s) vTaskDelay(pdMS_TO_TICKS(10));
            int kd = kind_of(mode);
            int32_t prompt = kd >= 0 ? idx[mode] % (nlines[kd] ? nlines[kd] : 1) : idx[mode];
            take_t *t = rec_stop(prompt, redo_of);
            redo_of = 0;
            char s[96]; int ms = (int)((esp_timer_get_time() - t0) / 1000);
            if (ms < 400) { t->acked = 1; snprintf(s, sizeof s, "Too short, hold TALK while you speak"); draw(s, false); continue; }
            last_id = t->id; last_prompt = prompt; last_mode = mode;
            idx[mode]++; nvs_save();
            if (t->range < 64) snprintf(s, sizeof s, "Saved #%lu  NO SOUND: check mic wires (CLK D4, DAT D5)", (unsigned long)t->id);
            else if (t->clip_pm > 5) snprintf(s, sizeof s, "Saved #%lu  TOO LOUD: hold it further away", (unsigned long)t->id);
            else if (t->peak < 1000) snprintf(s, sizeof s, "Saved #%lu  very quiet: speak up or closer", (unsigned long)t->id);
            else snprintf(s, sizeof s, "Saved #%lu (%.1f s). Next:", (unsigned long)t->id, t->n / (float)SR);
            draw(s, false);
        } else if (down(1)) {                                      /* BACK: redo previous / hold = speaker */
            int held = 0; while (down(1)) { vTaskDelay(pdMS_TO_TICKS(10)); held += 10;
                if (held == 700) { spk ^= 1; last_prompt = -1; nvs_save(); draw(spk ? "GUEST now: clips tagged as guest" : "ME now: clips tagged as you", false); } }
            if (held >= 700) continue;
            if (last_mode == mode && last_prompt >= 0) { idx[mode] = last_prompt; redo_of = last_id; nvs_save(); draw("Redo: read it again (old clip will be replaced)", false); }
        } else if (down(2)) {                                      /* DOWN: skip / hold = mode */
            int held = 0; while (down(2)) { vTaskDelay(pdMS_TO_TICKS(10)); held += 10; if (held == 700) { mode = (mode + 1) % M_COUNT; nvs_save(); draw(0, false); } }
            if (held < 700) { idx[mode]++; nvs_save(); draw("Skipped", false); }
        }
    }
}
