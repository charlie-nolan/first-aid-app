/* psram_test.c - bench soak test for the PSRAM speed experiment (Kconfig LIB_PSRAM_TEST).
 * A low-priority task on core 0 fills a PSRAM block with three patterns (address as
 * data, inverted, xorshift), reads each back, and times bulk copies. It runs forever
 * next to the normal UI and voice, so the board is under its real load. One log line
 * per pass:  PSRAM pass <n> err <total> wr <MB/s> rd <MB/s> cp <MB/s> temp <C>
 * Any err > 0 means the PSRAM clock is not stable on this board. */
#include "sdkconfig.h"
#if CONFIG_LIB_PSRAM_TEST
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "driver/temperature_sensor.h"

static const char *TAG = "psram";

static uint32_t xs(uint32_t *s) { uint32_t x = *s; x ^= x << 13; x ^= x >> 17; x ^= x << 5; return *s = x; }

static void task(void *arg) {
    size_t words = (size_t)CONFIG_LIB_PSRAM_TEST_KB * 1024 / 4;
    uint32_t *p = heap_caps_malloc(words * 4, MALLOC_CAP_SPIRAM);
    uint32_t *q = heap_caps_malloc(64 * 1024, MALLOC_CAP_SPIRAM);
    if (!p || !q) { ESP_LOGE(TAG, "could not get %d KB of PSRAM", CONFIG_LIB_PSRAM_TEST_KB); vTaskDelete(0); }
    temperature_sensor_handle_t ts = 0;
    temperature_sensor_config_t tc = TEMPERATURE_SENSOR_CONFIG_DEFAULT(10, 80);
    if (temperature_sensor_install(&tc, &ts) == ESP_OK) temperature_sensor_enable(ts);
    uint32_t err = 0;
    for (unsigned pass = 1;; pass++) {
        int64_t t_wr = 0, t_rd = 0;
        for (int pat = 0; pat < 3; pat++) {
            uint32_t seed = 0x9E3779B9u ^ pass, s = seed;
            int64_t t0 = esp_timer_get_time();
            for (size_t i = 0; i < words; i++) p[i] = pat == 0 ? (uint32_t)i : pat == 1 ? ~(uint32_t)i : xs(&s);
            int64_t t1 = esp_timer_get_time();
            s = seed;
            for (size_t i = 0; i < words; i++) {
                uint32_t want = pat == 0 ? (uint32_t)i : pat == 1 ? ~(uint32_t)i : xs(&s);
                if (p[i] != want) {
                    if (err < 20) ESP_LOGE(TAG, "mismatch at %p: %08lx want %08lx", &p[i], (unsigned long)p[i], (unsigned long)want);
                    err++;
                }
            }
            t_wr += t1 - t0; t_rd += esp_timer_get_time() - t1;
            vTaskDelay(1);                                  /* let the UI and voice in */
        }
        int64_t t2 = esp_timer_get_time();
        for (size_t off = 0; off + 16384 <= words; off += 16384) memcpy(q, p + off, 64 * 1024);
        int64_t t_cp = esp_timer_get_time() - t2;
        float mb = words * 4 / 1048576.0f, temp = 0;
        if (ts) temperature_sensor_get_celsius(ts, &temp);
        ESP_LOGI(TAG, "PSRAM pass %u err %lu wr %.1f rd %.1f cp %.1f MB/s temp %.0f C", pass, (unsigned long)err,
                 3 * mb / (t_wr / 1e6f), 3 * mb / (t_rd / 1e6f), mb / (t_cp / 1e6f), temp);
        vTaskDelay(pdMS_TO_TICKS(200));
    }
}
void psram_test_start(void) { xTaskCreatePinnedToCore(task, "psramtest", 4096, 0, 1, 0, 0); }
#else
void psram_test_start(void) {}
#endif
