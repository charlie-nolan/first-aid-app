/* vform bench: replay the embedded script (same format as components/vform/host_test.c) through
 * the C form engine on the board; print every SHOW frame, then per-frame timing. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "vform_show.h"
#include "asr.h"

extern const uint8_t pack_start[] asm("_binary_pack_fpk_start");
extern const uint8_t pack_end[] asm("_binary_pack_fpk_end");
extern const char script_start[] asm("_binary_script_txt_start");

static char *field(char **p) {
    char *s = *p, *t = strchr(s, '\t');
    if (t) { *t = 0; *p = t + 1; } else *p = s + strlen(s);
    return strcmp(s, "\\N") ? s : NULL;
}

static void run(void *arg) {
    vTaskDelay(pdMS_TO_TICKS(1500));                 /* let the PC open the port */
    fp_sess_t *s = heap_caps_calloc(1, sizeof *s, MALLOC_CAP_SPIRAM);
    char *out = heap_caps_malloc(32768, MALLOC_CAP_SPIRAM), *line = malloc(4096);
    size_t len = pack_end - pack_start;
    int64_t t0 = esp_timer_get_time();
    if (!s || !out || !line || !fp_sess_init(s, pack_start, len)) { printf("VFORM-BENCH init failed\n"); vTaskDelete(0); }
    printf("ASR engine slot: %s\n", asr_engine_name());
    printf("VFORM-BENCH 1 %d trees %u bytes, init %lld us, free internal %u psram %u\n", s->ntrees, (unsigned)len,
           esp_timer_get_time() - t0, heap_caps_get_free_size(MALLOC_CAP_INTERNAL), heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    static fp_screen_t sc;
    fp_screen_init(&sc, s);
    int64_t tsum = 0, tmax = 0; int frames = 0; char worst[96] = "";
    UBaseType_t stack0 = uxTaskGetStackHighWaterMark(0);
    for (const char *p = script_start; *p;) {
        const char *e = strchr(p, '\n');
        size_t L = e ? (size_t)(e - p) : strlen(p);
        if (L >= 4096) L = 4095;
        memcpy(line, p, L); line[L] = 0;
        p += L + (e ? 1 : 0);
        char *a = strchr(line, ' ');
        if (!a) continue;
        *a++ = 0;
        if (!strcmp(line, "CASE")) { fp_reset(s); fp_screen_init(&sc, s); printf("CASE %s\n", a); continue; }
        int64_t t = esp_timer_get_time();
        if (!strcmp(line, "SAY")) fp_screen_said(&sc, a, out, 32768);
        else if (!strcmp(line, "PICK")) { char *k = field(&a), *v = field(&a), *n = field(&a); fp_screen_picked(&sc, k, v, n, out, 32768); }
        else continue;
        t = esp_timer_get_time() - t;
        tsum += t; frames++;
        if (t > tmax) { tmax = t; snprintf(worst, sizeof worst, "%s %s", line, a); }
        printf("%s\n", out);
        vTaskDelay(1);
    }
    printf("TIMING %d frames, avg %lld us, max %lld us (%s), rank overflow %u, stack left %u bytes\n", frames,
           frames ? tsum / frames : 0, tmax, worst, (unsigned)s->rank_overflow, (unsigned)uxTaskGetStackHighWaterMark(0));
    (void)stack0;
    printf("VFORM-BENCH done\n");
    vTaskDelete(0);
}

void app_main(void) { xTaskCreatePinnedToCore(run, "vform", 32768, 0, 5, 0, 1); }
