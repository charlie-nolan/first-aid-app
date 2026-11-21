/* asr bench: the speech export's golden clips decoded on the S3. Prints words, reject and timing. */
#include <stdio.h>
#include <string.h>
#include "asr.h"
#include "fasr_q8.h"
#include "esp_heap_caps.h"
#include "esp_partition.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define CLIP(n) extern const uint8_t clip##n##_start[] asm("_binary_clip" #n "_bin_start");
CLIP(0) CLIP(1) CLIP(2) CLIP(3) CLIP(4) CLIP(5) CLIP(6) CLIP(7)

static void run(void *arg) {
    vTaskDelay(pdMS_TO_TICKS(1500));
    const esp_partition_t *p = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, "library");
    const void *rom = NULL; esp_partition_mmap_handle_t mh;
    if (!p || esp_partition_mmap(p, 0, p->size, ESP_PARTITION_MMAP_DATA, &rom, &mh) != ESP_OK) { printf("ASR-BENCH no library\n"); vTaskDelete(0); }
    {   /* kernel micro-benchmark: 16 x 16-byte blocks (one 256-channel pointwise output) */
        extern int32_t fq_dot16(const int8_t *a, const int8_t *b, int n16);
        static int8_t A[256] __attribute__((aligned(16))), Bv[256] __attribute__((aligned(16)));
        for (int i = 0; i < 256; i++) { A[i] = (int8_t)(i * 7); Bv[i] = (int8_t)(i * 13 + 1); }
        int32_t ref = 0; for (int i = 0; i < 256; i++) ref += A[i] * Bv[i];
        int8_t *P = heap_caps_aligned_alloc(16, 256 * 200, MALLOC_CAP_SPIRAM);
        for (int i = 0; i < 256 * 200; i++) P[i] = (int8_t)i;
        volatile int32_t sink = 0; int64_t k0 = esp_timer_get_time();
        for (int r = 0; r < 100000; r++) sink += fq_dot16(A, Bv, 16);
        int64_t k1 = esp_timer_get_time();
        for (int r = 0; r < 100000; r++) sink += fq_dot16(P + 256 * (r % 200), Bv, 16);
        int64_t k2 = esp_timer_get_time();
        printf("KERNEL dot256 %s: internal %.1f cycles, psram %.1f cycles per call\n", fq_dot16(A, Bv, 16) == ref ? "exact" : "WRONG",
               (k1 - k0) * 240.0 / 100000, (k2 - k1) * 240.0 / 100000);
    }
    int64_t t0 = esp_timer_get_time();
    asr_err_t e = asr_open_cart(rom, p->size);
    printf("ASR-BENCH internal free %u largest %u\n", heap_caps_get_free_size(MALLOC_CAP_INTERNAL), heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
    printf("ASR-BENCH 1 engine %s open %d (%lld us), psram free %u\n", asr_engine_name(), (int)e,
           esp_timer_get_time() - t0, heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    const uint8_t *clips[8] = {clip0_start, clip1_start, clip2_start, clip3_start, clip4_start, clip5_start, clip6_start, clip7_start};
    for (int i = 0; i < N_CLIPS && i < 8 && e == ASR_OK; i++) {
        const uint8_t *c = clips[i];
        uint32_t n = c[0] | c[1] << 8 | c[2] << 16 | (uint32_t)c[3] << 24;
        static asr_hyp_t h;
        int64_t t = esp_timer_get_time();
        asr_decode((const int16_t *)(c + 4), n, &h);
        t = esp_timer_get_time() - t;
        uint32_t a, b, d; asr_last_timing(&a, &b, &d);
        printf("CLIP %02d %.2f s audio: total %lld ms (front end %lu, model %lu, search %lu), reject %d raw %d%s:", i, n / 16000.0,
               t / 1000, (unsigned long)(a / 1000), (unsigned long)(b / 1000), (unsigned long)(d / 1000), h.reject, h.reject_raw, h.veto ? " veto" : "");
        for (int k = 0; h.n && k < h.h[0].len; k++) { const char *w = asr_word(h.h[0].id[k]); printf(" %s", w ? w : "<unk>"); }
        printf("\n");
        printf("PROF %02d quant %lu pw %lu dw %lu other %lu softmax %lu ms\n", i, fq_prof_us[0] / 1000, fq_prof_us[1] / 1000,
               fq_prof_us[2] / 1000, fq_prof_us[3] / 1000, fq_prof_us[4] / 1000);
        memset(fq_prof_us, 0, sizeof fq_prof_us);
        vTaskDelay(1);
    }
    printf("ASR-BENCH done\n");
    vTaskDelete(0);
}
void app_main(void) { xTaskCreatePinnedToCore(run, "asr", 16384, 0, 5, 0, 1); }
