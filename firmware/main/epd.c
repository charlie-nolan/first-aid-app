/* epd.c - SSD1680 driver for the Waveshare 2.13" V4, following Waveshare's
 * EPD_2in13_V4.c sequences (init, base image, partial update, sleep).
 * Panel RAM is portrait: 122 px wide (16 bytes/row), 250 rows, 1 = white. */
#include "epd.h"
#include "board.h"
#include <string.h>
#include "driver/spi_master.h"
#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"

#define PW 122
#define PH 250
#define PBPR 16                            /* bytes per panel row */
static const char *TAG = "epd";
static spi_device_handle_t spi;
static uint8_t ram[PBPR * PH];             /* panel image, built from the landscape fb */
static bool awake;
static bool busy_warned;

static void cmd(uint8_t c) {
    gpio_set_level(PIN_EPD_DC, 0);
    spi_transaction_t t = { .length = 8, .flags = SPI_TRANS_USE_TXDATA, .tx_data = { c } };
    spi_device_polling_transmit(spi, &t);
}
static void data(const uint8_t *d, size_t n) {
    gpio_set_level(PIN_EPD_DC, 1);
    while (n) {
        size_t k = n > 4000 ? 4000 : n;
        spi_transaction_t t = { .length = 8 * k, .tx_buffer = d };
        spi_device_polling_transmit(spi, &t);
        d += k; n -= k;
    }
}
static void data1(uint8_t b) { data(&b, 1); }
static void cmdv(uint8_t c, const uint8_t *d, size_t n) { cmd(c); if (n) data(d, n); }

static int wait_busy(void) {                /* BUSY is high while the panel works; returns ms waited */
    int ms = 0;
    while (gpio_get_level(PIN_EPD_BUSY) == 1) {
        vTaskDelay(pdMS_TO_TICKS(5));
        if ((ms += 5) > 6000) { ESP_LOGW(TAG, "BUSY stuck high: check the BUSY wire"); break; }
    }
    return ms;
}
/* Start the refresh and wait for it. A refresh takes ~2 s (full) or ~0.5 s (partial);
 * if BUSY never goes high the wire is loose or missing, and sending the next image
 * mid-refresh tears the screen, so fall back to a fixed wait. */
static void update(uint8_t mode, bool full) {
    cmdv(0x22, &mode, 1);
    cmd(0x20);
    vTaskDelay(pdMS_TO_TICKS(2));
    int ms = wait_busy();
    if (ms < 20) {
        if (!busy_warned) ESP_LOGW(TAG, "BUSY never went high: check the BUSY wire (D3). Using fixed waits.");
        busy_warned = true;
        vTaskDelay(pdMS_TO_TICKS(full ? 3000 : 800));
    }
    ESP_LOGI(TAG, "%s refresh, BUSY high for %d ms", full ? "full" : "partial", ms);
}
static void reset(void) {
    gpio_set_level(PIN_EPD_RST, 1); vTaskDelay(pdMS_TO_TICKS(20));
    gpio_set_level(PIN_EPD_RST, 0); vTaskDelay(pdMS_TO_TICKS(2));
    gpio_set_level(PIN_EPD_RST, 1); vTaskDelay(pdMS_TO_TICKS(20));
}
static void window_full(void) {
    cmdv(0x44, (const uint8_t[]){ 0x00, (PW - 1) >> 3 }, 2);
    cmdv(0x45, (const uint8_t[]){ 0x00, 0x00, (PH - 1) & 0xFF, (PH - 1) >> 8 }, 4);
    cmdv(0x4E, (const uint8_t[]){ 0x00 }, 1);
    cmdv(0x4F, (const uint8_t[]){ 0x00, 0x00 }, 2);
}
static void panel_init(void) {
    reset(); wait_busy();
    cmd(0x12);                                                   /* software reset */
    vTaskDelay(pdMS_TO_TICKS(20));      /* datasheet: 10 ms before the next command, even if BUSY reads low */
    wait_busy();
    cmdv(0x01, (const uint8_t[]){ (PH - 1) & 0xFF, (PH - 1) >> 8, 0x00 }, 3);   /* gate lines */
    cmdv(0x11, (const uint8_t[]){ 0x03 }, 1);                    /* X+, Y+ */
    window_full();
    cmdv(0x3C, (const uint8_t[]){ 0x05 }, 1);                    /* border */
    cmdv(0x21, (const uint8_t[]){ 0x00, 0x80 }, 2);
    cmdv(0x18, (const uint8_t[]){ 0x80 }, 1);                    /* internal temperature sensor */
    wait_busy();
    awake = true;
}

void epd_init(void) {
    gpio_config_t o = { .pin_bit_mask = (1ULL << PIN_EPD_DC) | (1ULL << PIN_EPD_RST), .mode = GPIO_MODE_OUTPUT };
    gpio_config(&o);
    gpio_config_t i = { .pin_bit_mask = 1ULL << PIN_EPD_BUSY, .mode = GPIO_MODE_INPUT, .pull_down_en = 1 };
    gpio_config(&i);
    spi_bus_config_t bus = { .mosi_io_num = PIN_EPD_MOSI, .miso_io_num = -1, .sclk_io_num = PIN_EPD_SCK,
                             .quadwp_io_num = -1, .quadhd_io_num = -1, .max_transfer_sz = 4096 };
    ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO));
    spi_device_interface_config_t dev = { .clock_speed_hz = 4 * 1000 * 1000   /* 4 MHz: clean edges over breadboard jumpers */, .mode = 0,
                                          .spics_io_num = PIN_EPD_CS, .queue_size = 1 };
    ESP_ERROR_CHECK(spi_bus_add_device(SPI2_HOST, &dev, &spi));
    panel_init();
}

/* landscape (x 0..249, y 0..121) -> panel (col 0..121, row 0..249) */
static void build_ram(const uint8_t *fb) {
    memset(ram, 0xFF, sizeof ram);
    for (int y = 0; y < PW; y++)
        for (int x = 0; x < PH; x++)
            if (fb[y * 32 + (x >> 3)] & (0x80 >> (x & 7))) {
#if CONFIG_LIB_PANEL_FLIP
                int col = y, row = PH - 1 - x;
#else
                int col = PW - 1 - y, row = x;
#endif
                ram[row * PBPR + (col >> 3)] &= ~(0x80 >> (col & 7));
            }
}

/* Write the image to both RAMs: 0x24 (what to show) and 0x26 (the base the
 * next partial update diffs against), so the base always matches the glass. */
static void write_both(void) {
    window_full();
    cmdv(0x24, ram, sizeof ram);
    window_full();
    cmdv(0x26, ram, sizeof ram);
}

void epd_show(const uint8_t *fb, int mode) {
    if (!awake) { panel_init(); mode = EPD_FULL; }
    build_ram(fb);
    if (mode == EPD_FULL) {
        cmdv(0x3C, (const uint8_t[]){ 0x05 }, 1);
        write_both();
        update(0xF7, true);
    } else if (mode == EPD_CLEAN) {
        /* Waveshare's 2.13" V4 "fast" refresh, the SSD1680 version of CrossInk's
         * single-pass clean-up: tell the controller it is 100 C so it loads its
         * short full waveform (0x22 0x91 loads it), then run that waveform alone
         * (0xC7). One gentle flash instead of the multi-flash full refresh.
         * The next partial (0x22 0xFF) reloads the real temperature itself. */
        cmdv(0x3C, (const uint8_t[]){ 0x05 }, 1);
        cmdv(0x1A, (const uint8_t[]){ 0x64, 0x00 }, 2);
        cmdv(0x22, (const uint8_t[]){ 0x91 }, 1);
        cmd(0x20); wait_busy();
        write_both();
        update(0xC7, true);
    } else {
        /* No hardware reset here: it is not needed between partials and the
         * controller wants ~10 ms after one before it takes commands. */
        cmdv(0x3C, (const uint8_t[]){ 0x80 }, 1);
        cmdv(0x01, (const uint8_t[]){ (PH - 1) & 0xFF, (PH - 1) >> 8, 0x00 }, 3);
        cmdv(0x11, (const uint8_t[]){ 0x03 }, 1);
        window_full();
        cmdv(0x24, ram, sizeof ram);
        update(0xFF, false);
        /* Keep the base image (0x26) equal to what is now on the glass, so the
         * next partial update diffs against the real screen. Without this the
         * base stays at the last full refresh and errors build up page by page. */
        window_full();
        cmdv(0x26, ram, sizeof ram);
    }
}

void epd_sleep(void) {
    if (!awake) return;
    cmd(0x10); data1(0x01);                          /* deep sleep mode 1: keeps the image, needs a reset to wake */
    vTaskDelay(pdMS_TO_TICKS(100));
    awake = false;
}
