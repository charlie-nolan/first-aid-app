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
#include "board.h"
#include "app.h"
#include "power.h"
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

void voice_load(uint16_t tree, const cart_tags_t *ctx) {
    if (!md) return;
    esp_mn_commands_clear();
    int n = 0;
    if (cart_phrase_count(cart)) {                          /* per-section list from the cartridge */
        static uint16_t ids[CART_VOICE_MAX];
        cart_tags_t any = {{0}};
        int k = cart_voice_list(cart, tree, ctx ? ctx : &any, ids, CART_VOICE_MAX);
        for (int i = 0; i < k; i++) {
            char s[64]; cart_text(cart, cart_phrase(cart, ids[i]).text, 0, s, sizeof s);
            if (s[0] && esp_mn_commands_add(ids[i], s) == ESP_OK) n++;
        }
    } else {                                                 /* old cartridge: embedded phrases.txt */
        for (const char *p = phrases_start; p < phrases_end; ) {     /* "id\tphrase\n" */
            const char *e = memchr(p, '\n', phrases_end - p); if (!e) e = phrases_end;
            const char *tab = memchr(p, '\t', e - p);
            if (tab) {
                char s[64]; int len = e - tab - 1; if (len > 63) len = 63;
                memcpy(s, tab + 1, len); s[len] = 0;
                if (len && s[len - 1] == '\r') s[len - 1] = 0;
                if (esp_mn_commands_add(atoi(p), s) == ESP_OK) n++;
            }
            p = e + 1;
        }
    }
    esp_mn_commands_update();
    ESP_LOGI(TAG, "voice list: %d phrases (section %d)", n, tree == CART_NONE ? -1 : tree);
}

static void post(const esp_mn_results_t *r) {
    app_msg_t m = { .type = MSG_VOICE };
    for (int i = 0; r && i < r->num && i < 3; i++) { m.cmd[i] = r->command_id[i]; m.prob[i] = r->prob[i]; m.n = (int8_t)(i + 1); }
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
        if (want_result) post(hit);
        want_result = false;
    }
}

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
