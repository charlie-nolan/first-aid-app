/* voice.c - push-to-talk speech commands with ESP-SR MultiNet (no wake word,
 * no AFE: TALK is the trigger). The command list comes from library/phrases.txt
 * ("id<TAB>phrase" lines, written by the pipeline) embedded in the app; the
 * command id is the ROM's CMDS index, so cart_cmd_node() maps it to a page.
 * Audio: Adafruit #3492 PDM mic, SEL to GND, 16 kHz mono through I2S0 PDM RX. */
#include "voice.h"
#include "sdkconfig.h"
#if CONFIG_LIB_VOICE
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include "board.h"
#include "app.h"
#include "power.h"
#include "ui.h"
#include "esp_timer.h"
#include "driver/i2s_pdm.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_mn_iface.h"
#include "esp_mn_models.h"
#include "esp_mn_speech_commands.h"
#include "model_path.h"

static const char *TAG = "voice";
extern const char phrases_start[] asm("_binary_phrases_txt_start");
extern const char phrases_end[] asm("_binary_phrases_txt_end");

static i2s_chan_handle_t rx;
static esp_mn_iface_t *mn;
static model_iface_data_t *md;
static int chunk;                        /* samples per detect() call */
static volatile bool listening, want_result;
static SemaphoreHandle_t go;
static const cart_t *cart;

/* Local MultiNet ids -> what ui_voice gets: a cartridge command id, or UI_VOICE_KEY(k).
 * Local ids 0..VK_COUNT-1 are the voice keys (each with a few synonyms), then phrases. */
#define MAP_MAX (CART_VOICE_MAX + VK_COUNT)
static int map[MAP_MAX], nmap;
static int64_t list_us;                  /* last list swap time, for the bench log */

#if CONFIG_LIB_VOICE_NUMBERS
/* Inside a topic the answer tiles are numbered 1-4 (reading order: TL TR BL BR), so the
 * list there is just these words. "to"/"too" and "for" can't be mistaken for anything
 * else in so small a list; they simply land on two and four. The "number"/"option"
 * forms are longer, so MultiNet has more sound to go on in wind. */
static const char *const KEY_WORDS[VK_COUNT][4] = {
    [VK_1] = { "one", "number one", "option one", 0 },
    [VK_2] = { "two", "number two", "option two", 0 },
    [VK_3] = { "three", "number three", "option three", 0 },
    [VK_4] = { "four", "number four", "option four", 0 },
    [VK_BACK] = { "back", "go back", 0, 0 },
    [VK_AGAIN] = { "again", "read again", "repeat", 0 },
    [VK_HOME] = { "home", "go home", "start over", 0 },
};
static int add_keys(void) {
    int n = 0;
    for (int k = 0; k < VK_COUNT; k++) {
        map[k] = UI_VOICE_KEY(k);
        for (int j = 0; j < 4 && KEY_WORDS[k][j]; j++)
            if (esp_mn_commands_add(k, KEY_WORDS[k][j]) == ESP_OK) n++;
    }
    nmap = VK_COUNT;
    return n;
}
/* In a topic the numbers come first; next to them stay the phrases that change plans:
 * `always: true` phrases, and every section's entry phrase (LIB_VOICE_TOPICS_EVERYWHERE,
 * default) or only emergency sections' entries. A section's own per-answer phrases
 * are left out: inside a topic the numbers do that job. */
static int jump_phrase(uint16_t id) {
    cart_phrase_t p = cart_phrase(cart, id);
    if (p.flags & CART_PHRASE_ALWAYS) return 1;
#if CONFIG_LIB_VOICE_TOPICS_EVERYWHERE
    if (p.flags & CART_PHRASE_ENTRY) return 1;    /* Charlie 11-13: every topic phrase stays live */
#endif
    const cart_node_t *n = (p.flags & CART_PHRASE_ENTRY) ? cart_node(cart, p.node) : 0;
    return n && (n->flags & CART_NODE_EMERGENCY);
}
#endif

static int add_phrase(uint16_t cmd_id) {
    if (nmap >= MAP_MAX) return 0;
    char s[64]; cart_text(cart, cart_phrase(cart, cmd_id).text, 0, s, sizeof s);
    if (!s[0] || esp_mn_commands_add(nmap, s) != ESP_OK) return 0;
    map[nmap++] = cmd_id;
    return 1;
}

void voice_load(uint16_t tree, const cart_tags_t *ctx) {
    if (!md) return;
    int64_t t0 = esp_timer_get_time();
    esp_mn_commands_clear();
    nmap = 0;
    int n = 0, jumps = 0;
#if CONFIG_LIB_VOICE_NUMBERS
    n += add_keys();
#endif
    if (cart_phrase_count(cart)) {                          /* per-section list from the cartridge */
        static uint16_t ids[CART_VOICE_MAX];
        cart_tags_t any = {{0}};
        int cap = CART_VOICE_MAX - n;                        /* MultiNet counts phrases, synonyms included */
        int k = cart_voice_list(cart, tree, ctx ? ctx : &any, ids, cap);
        for (int i = 0; i < k; i++) {
#if CONFIG_LIB_VOICE_NUMBERS
            if (tree != CART_NONE && !jump_phrase(ids[i])) continue;   /* in a topic: numbers + jumps only */
#if !CONFIG_LIB_VOICE_TOPICS_EVERYWHERE
            if (tree != CART_NONE && ++jumps > CONFIG_LIB_VOICE_MAX_JUMPS) break;
#endif
#endif
            n += add_phrase(ids[i]);
        }
    } else {                                                 /* old cartridge: embedded phrases.txt */
        for (const char *p = phrases_start; p < phrases_end && nmap < MAP_MAX; ) {     /* "id\tphrase\n" */
            const char *e = memchr(p, '\n', phrases_end - p); if (!e) e = phrases_end;
            const char *tab = memchr(p, '\t', e - p);
            if (tab) {
                char s[64]; int len = e - tab - 1; if (len > 63) len = 63;
                memcpy(s, tab + 1, len); s[len] = 0;
                if (len && s[len - 1] == '\r') s[len - 1] = 0;
                if (esp_mn_commands_add(nmap, s) == ESP_OK) { map[nmap++] = atoi(p); n++; }
            }
            p = e + 1;
        }
    }
    esp_mn_commands_update();
    list_us = esp_timer_get_time() - t0;
    ESP_LOGI(TAG, "voice list: %d phrases, %d ids (section %d), swap %lld ms", n, nmap,
             tree == CART_NONE ? -1 : tree, (long long)(list_us / 1000));
    (void)jumps;
}

static void post(const esp_mn_results_t *r) {
    app_msg_t m = { .type = MSG_VOICE };
    for (int i = 0; r && i < r->num && i < 3; i++) {
        int id = r->command_id[i];
        m.cmd[i] = id >= 0 && id < nmap ? map[id] : -1;
        m.prob[i] = r->prob[i]; m.n = (int8_t)(i + 1);
    }
#if CONFIG_LIB_VOICE_BENCH
    /* one machine-readable line per result: VB <n> <cmd>:<prob>:<phrase>|... */
    printf("VB %d", m.n);
    for (int i = 0; i < m.n; i++) {
        char s[64] = "";
        if (UI_VOICE_IS_KEY(m.cmd[i])) snprintf(s, sizeof s, "key:%s", UI_VOICE_KEY_NAME(UI_VOICE_KEY_OF(m.cmd[i])));
        else if (m.cmd[i] >= 0 && cart_phrase_count(cart)) cart_text(cart, cart_phrase(cart, (uint16_t)m.cmd[i]).text, 0, s, sizeof s);
        printf("%s%d:%.3f:%s", i ? "|" : " ", m.cmd[i], m.prob[i], s);
    }
    printf("\n");
    fflush(stdout);
    return;             /* bench build: results only go to serial, so the screen and list stay put */
#endif
    xQueueSend(app_q, &m, 0);
}

static void task(void *arg) {
    int16_t *buf = heap_caps_malloc(chunk * sizeof(int16_t), MALLOC_CAP_INTERNAL);
    for (;;) {
        xSemaphoreTake(go, portMAX_DELAY);
        mn->clean(md);
        i2s_channel_enable(rx);
        const esp_mn_results_t *hit = 0;
        while (listening && !hit) {
            size_t got = 0;
            i2s_channel_read(rx, buf, chunk * sizeof(int16_t), &got, pdMS_TO_TICKS(200));
            if (got < chunk * sizeof(int16_t)) continue;
            if (mn->detect(md, buf) == ESP_MN_STATE_DETECTED) hit = mn->get_results(md);
        }
        int64_t t_rel = esp_timer_get_time();
        /* released before MultiNet decided: give it ~0.6 s of trailing silence to finish */
        if (!hit && want_result) {
            memset(buf, 0, chunk * sizeof(int16_t));
            for (int i = 0; i < 20 && !hit; i++) {
                esp_mn_state_t st = mn->detect(md, buf);
                if (st == ESP_MN_STATE_DETECTED) hit = mn->get_results(md);
                else if (st == ESP_MN_STATE_TIMEOUT) break;
            }
        }
        i2s_channel_disable(rx);
        power_mic(false);
        if (want_result) {
            ESP_LOGI(TAG, "result %d after release: %lld ms", hit ? hit->num : 0,
                     (long long)((esp_timer_get_time() - t_rel) / 1000));
            post(hit);
        }
        want_result = false;
    }
}

#if CONFIG_LIB_VOICE_BENCH
/* Bench mode: tools/gpu/bench_loop.py plays a clip into the mic and sends
 * "L <ms>"; we listen that long and print a VB line. Polls stdin (the USB
 * serial console reads without blocking when no driver is installed). */
static void bench_task(void *arg) {
    char line[32]; int n = 0;
    printf("VB ready\n"); fflush(stdout);
    for (;;) {
        int ch = getchar();
        if (ch == EOF) { vTaskDelay(pdMS_TO_TICKS(10)); continue; }
        if (ch != '\n' && ch != '\r') { if (n < (int)sizeof line - 1) line[n++] = (char)ch; continue; }
        line[n] = 0; n = 0;
        if (line[0] == 'L') {
            int ms = atoi(line + 1); if (ms < 200 || ms > 8000) ms = 3000;
            voice_start();
            vTaskDelay(pdMS_TO_TICKS(ms));
            voice_stop(true);
        } else if (line[0] == 'T') {
            int t = atoi(line + 1);
            voice_load(t < 0 ? CART_NONE : (uint16_t)t, 0);
            printf("VB list %d\n", t); fflush(stdout);
        }
    }
}
#endif

bool voice_init(const cart_t *c) {
    srmodel_list_t *models = esp_srmodel_init("model");
    char *name = models ? esp_srmodel_filter(models, ESP_MN_PREFIX, ESP_MN_ENGLISH) : 0;
    if (!name) { ESP_LOGE(TAG, "no English MultiNet model in the 'model' partition"); return false; }
    mn = esp_mn_handle_from_name(name);
    md = mn->create(name, 4000);                       /* max phrase length, ms */
    esp_mn_commands_alloc(mn, md);
    cart = c;
    voice_load(CART_NONE, 0);
    chunk = mn->get_samp_chunksize(md);
    ESP_LOGI(TAG, "%s, chunk %d samples", name, chunk);

    i2s_chan_config_t cc = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    ESP_ERROR_CHECK(i2s_new_channel(&cc, NULL, &rx));
    i2s_pdm_rx_config_t pc = {
        .clk_cfg = I2S_PDM_RX_CLK_DEFAULT_CONFIG(16000),
        .slot_cfg = I2S_PDM_RX_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = { .clk = PIN_MIC_CLK, .din = PIN_MIC_DATA, .invert_flags = { .clk_inv = false } },
    };
    ESP_ERROR_CHECK(i2s_channel_init_pdm_rx_mode(rx, &pc));
    go = xSemaphoreCreateBinary();
    xTaskCreatePinnedToCore(task, "voice", 8192, 0, 6, 0, 1);
#if CONFIG_LIB_VOICE_BENCH
    xTaskCreatePinnedToCore(bench_task, "vbench", 4096, 0, 5, 0, 0);
#endif
    return true;
}

void voice_start(void) {
    if (!rx || listening) return;
    power_mic(true);                     /* mic needs a few ms to settle; MultiNet ignores the first chunk anyway */
    listening = true; want_result = false;
    xSemaphoreGive(go);
}
void voice_stop(bool recognise) { if (!listening) return; want_result = recognise; listening = false; }

#else
bool voice_init(const cart_t *c) { (void)c; return false; }
void voice_load(uint16_t tree, const cart_tags_t *ctx) { (void)tree; (void)ctx; }
void voice_start(void) {}
void voice_stop(bool recognise) { (void)recognise; }
#endif
