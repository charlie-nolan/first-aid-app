/* main.c - Pocket First-Aid Librarian firmware.
 *
 * Boot (cold or from deep sleep): mmap the library partition, open the
 * cartridge, restore the 5 reading windows from RTC memory, show the wake
 * screen ("Life threat? Call 911 / SOS first."). Then handle buttons and voice
 * until idle, draw the sleep card, and deep sleep until a button is pressed. */
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_partition.h"
#include "esp_timer.h"
#include "esp_attr.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "board.h"
#include "app.h"
#include "ui.h"
#include "epd.h"
#include "buttons.h"
#include "lights.h"
#include "power.h"
#include "voice.h"
#include "cart.h"

static const char *TAG = "librarian";
QueueHandle_t app_q;
RTC_DATA_ATTR static ui_saved_t saved;          /* the 5 windows, text size, light level: survive deep sleep */
RTC_DATA_ATTR static uint32_t crc_ok_build;     /* skip the full-ROM CRC after a wake if it already passed */

static cart_t cart;
static ui_t ui;
static uint8_t fb_bits[UI_STRIDE * UI_H];
static cart_fb_t fb = { fb_bits, UI_STRIDE, UI_W, UI_H };
static int partials;
static uint16_t voice_tree = 0xFFFE;               /* section the voice list was built for */
static cart_tags_t voice_ctx;

/* The sticky context (state, season, activity) lives in NVS so it survives
 * power loss and reflashing the app, until the reader picks another. */
static void ctx_load(cart_tags_t *ctx) {
    nvs_handle_t h; size_t n = sizeof *ctx;
    if (nvs_open("librarian", NVS_READONLY, &h) == ESP_OK) {
        nvs_get_blob(h, "ctx", ctx, &n);
        nvs_get_u8(h, "clean", &saved.clean);          /* clean-up interval, menu setting */
        nvs_close(h);
    }
}
static void ctx_save(const cart_tags_t *ctx) {
    nvs_handle_t h;
    if (nvs_open("librarian", NVS_READWRITE, &h) == ESP_OK) {
        nvs_set_blob(h, "ctx", ctx, sizeof *ctx);
        nvs_set_u8(h, "clean", saved.clean);
        nvs_commit(h); nvs_close(h);
    }
}
static void voice_follow(void) {                    /* new section or context: swap the phrase list */
    if (!ui.has_voice) return;
    const cart_node_t *n = cart_node(&cart, saved.win[saved.cur].node);
    uint16_t t = n ? n->tree : CART_NONE;
    if (t == voice_tree && !memcmp(&voice_ctx, &saved.ctx, sizeof voice_ctx)) return;
    voice_tree = t; voice_ctx = saved.ctx;
    voice_load(t, &saved.ctx);
}

static int clean_every(void) {                  /* page turns between clean-ups (menu setting) */
    return saved.clean ? saved.clean : CONFIG_LIB_FULL_REFRESH_EVERY;
}
static void show(int how) {
    if (how == RD_NONE) return;
    ui_render(&ui, &fb);
    /* Page turns are quick partial refreshes; every N of them (default 15, as
     * in CrossInk) one gentle clean-up flash clears the ghosts. Screens that
     * ask for a full redraw get the same gentle clean. */
    int mode = EPD_PARTIAL;
    if (how == RD_FULL || ++partials >= clean_every()) mode = EPD_CLEAN;
    if (mode != EPD_PARTIAL) partials = 0;
    epd_show(fb_bits, mode);
}

/* Any two buttons pressed together = full screen refresh (clears ghosting).
 * Both buttons are then ignored until each is released, so the combo does
 * nothing else. Returns true when the event is used up here. */
static uint8_t held_mask, swallow_mask;
static bool combo_filter(const app_msg_t *m) {
    uint8_t bit = (uint8_t)(1u << m->btn);
    if (m->kind == E_PRESS) {
        if (held_mask && !(held_mask & bit)) {
            swallow_mask |= held_mask | bit;
            held_mask |= bit;
            if (ui.talk_armed || ui.listening) { ui.talk_armed = 0; ui.listening = 0; voice_stop(false); }
            ESP_LOGI(TAG, "two-button refresh");
            partials = 0;
            ui_render(&ui, &fb);
            epd_show(fb_bits, EPD_FULL);             /* the deep clean, on request */
            return true;
        }
        held_mask |= bit;
    } else if (m->kind == E_RELEASE) {
        held_mask &= (uint8_t)~bit;
        if (swallow_mask & bit) { swallow_mask &= (uint8_t)~bit; return true; }
    }
    return (swallow_mask & bit) != 0;
}

static void go_to_sleep(uint32_t timer_s) {
    ESP_LOGI(TAG, "sleeping");
    voice_stop(false);
    ui_render_sleep(&ui, &fb);
    epd_show(fb_bits, EPD_CLEAN);
    epd_sleep();
    lights_off_for_sleep();
    power_sleep(timer_s, saved.mesh_on);
}

static bool open_library(void) {
    const esp_partition_t *p = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, "library");
    if (!p) { ESP_LOGE(TAG, "no 'library' partition: check the partition table"); return false; }
    const void *rom; esp_partition_mmap_handle_t h;
    if (esp_partition_mmap(p, 0, p->size, ESP_PARTITION_MMAP_DATA, &rom, &h) != ESP_OK) { ESP_LOGE(TAG, "mmap failed"); return false; }
    uint32_t build = ((const uint8_t *)rom)[24] | ((const uint8_t *)rom)[25] << 8 | ((const uint8_t *)rom)[26] << 16 | (uint32_t)((const uint8_t *)rom)[27] << 24;
    int rc = cart_open(&cart, rom, p->size, crc_ok_build != build);
    if (rc) { ESP_LOGE(TAG, "cart_open failed (%d): flash library.bin (idf.py flash does it)", rc); crc_ok_build = 0; return false; }
    crc_ok_build = cart.build_id;
    ESP_LOGI(TAG, "library %08x: %u nodes, %u trees, %u bytes", (unsigned)cart.build_id, cart.n_nodes, cart.n_trees, (unsigned)cart.size);
    return true;
}

static void update_battery(void) {
    battery_t b = power_battery();
    static int8_t last_pct = -2;
    if (b.pct != last_pct) {                            /* log changes only, so serial shows what the header shows */
        if (b.pct < 0) ESP_LOGI(TAG, "battery: not measured (no divider on this board, or reading out of range)");
        else ESP_LOGI(TAG, "battery: %d mV, %d%%%s%s", b.mv, b.pct, b.charging ? ", charging" : "", b.reserve ? ", reserve" : "");
        last_pct = b.pct;
    }
    ui.battery_pct = b.pct; ui.charging = b.charging; ui.low_battery = b.reserve;
    if (b.critical && !b.charging && !lights_signal_active()) {
        ESP_LOGW(TAG, "battery critical (%d mV): sleeping, will check again in 5 min", b.mv);
        go_to_sleep(300);
    }
}

void app_main(void) {
    ESP_LOGI(TAG, "board: %s", BOARD_NAME);
    app_q = xQueueCreate(16, sizeof(app_msg_t));
    if (nvs_flash_init() != ESP_OK) { nvs_flash_erase(); nvs_flash_init(); }
    lights_init();
    power_init();
    bool woke = power_woke_from_sleep();
    if (!woke) memset(&saved, 0, sizeof saved);      /* cold boot: fresh windows */

    if (!open_library()) {                           /* no cartridge: blink the front light and stay awake */
        for (;;) { lights_front(1); vTaskDelay(pdMS_TO_TICKS(300)); lights_front(0); vTaskDelay(pdMS_TO_TICKS(700)); }
    }
    ui_init(&ui, &cart, &saved);
    if (!woke) ctx_load(&saved.ctx);
    ui.has_window_keys = HAS_WINDOW_KEYS;
    ui.has_ir = PIN_IR >= 0;
    ui.has_mesh = PIN_MESH_PWR >= 0;
    power_mesh(saved.mesh_on);
    lights_front(saved.light);
    update_battery();

    epd_init();
    show(RD_FULL);                                   /* wake screen */
    buttons_init();
    ui.has_voice = voice_init(&cart);
    voice_follow();
    if (ui.has_voice) ESP_LOGI(TAG, "voice ready: hold TALK and speak");

    int64_t last = esp_timer_get_time(), last_bat = last;
    for (;;) {
        app_msg_t m;
        if (xQueueReceive(app_q, &m, pdMS_TO_TICKS(1000))) {
            last = esp_timer_get_time();
            if (m.type == MSG_BUTTON && combo_filter(&m)) continue;
            ui_act_t a = m.type == MSG_BUTTON ? ui_event(&ui, (ui_btn_t)m.btn, (ui_evkind_t)m.kind, m.held_ms)
                                              : ui_voice(&ui, m.cmd, m.prob, m.n);
            if (a.voice == VO_START) voice_start();
            else if (a.voice == VO_STOP) voice_stop(true);
            else if (a.voice == VO_CANCEL) voice_stop(false);
            if (a.light_step) { saved.light = (uint8_t)((saved.light + 1) % 3); lights_front(saved.light); }
            if (a.signal >= 0) lights_signal((uint8_t)a.signal);
            if (a.mesh) power_mesh(saved.mesh_on);
            if (a.ctx_changed) ctx_save(&saved.ctx);
            if (a.sleep_now) go_to_sleep(0);
            if (!ui.listening) voice_follow();
            show(a.redraw);
        }
        int64_t now = esp_timer_get_time();
        if (now - last_bat > 10 * 1000000LL) { last_bat = now; update_battery(); }
        bool busy = lights_signal_active() || ui.listening;
#if CONFIG_LIB_VOICE_BENCH
        busy = true;                                 /* bench mode: stay awake for the clip loop */
#endif
        if (!busy && now - last > (int64_t)CONFIG_LIB_IDLE_SECONDS * 1000000LL) go_to_sleep(0);
    }
}
