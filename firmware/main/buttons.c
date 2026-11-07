/* buttons.c - polled every 10 ms (debounce = 3 equal samples). Each press
 * posts E_PRESS, then E_SHORT (released before 700 ms) or E_LONG (at 700 ms,
 * while still held), then E_RELEASE with how long it was held.
 * A button already held at boot (the one that woke us) is ignored until released. */
#include "buttons.h"
#include "board.h"
#include "app.h"
#include "driver/gpio.h"
#include "freertos/task.h"

#define LONG_MS 700
static const int pins[B_COUNT] = { PIN_BTN_TALK, PIN_BTN_BACK, PIN_BTN_WIN_L, PIN_BTN_WIN_R, PIN_BTN_UP, PIN_BTN_DOWN };

static void post(int b, int kind, int held) {
    app_msg_t m = { .type = MSG_BUTTON, .btn = (uint8_t)b, .kind = (uint8_t)kind, .held_ms = (int16_t)(held > 30000 ? 30000 : held) };
    xQueueSend(app_q, &m, 0);
}

static void task(void *arg) {
    uint8_t stable[B_COUNT], cnt[B_COUNT] = {0}, ignore[B_COUNT] = {0}, longsent[B_COUNT] = {0};
    int held[B_COUNT] = {0};
    for (int b = 0; b < B_COUNT; b++) {
        stable[b] = pins[b] >= 0 && gpio_get_level(pins[b]) == 0;
        ignore[b] = stable[b];
    }
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(10));
        for (int b = 0; b < B_COUNT; b++) {
            if (pins[b] < 0) continue;
            uint8_t down = gpio_get_level(pins[b]) == 0;
            if (down != stable[b]) { if (++cnt[b] >= 3) { stable[b] = down; cnt[b] = 0;
                    if (down) { held[b] = 0; longsent[b] = 0; if (!ignore[b]) post(b, E_PRESS, 0); }
                    else if (ignore[b]) ignore[b] = 0;
                    else { if (!longsent[b]) post(b, E_SHORT, held[b]); post(b, E_RELEASE, held[b]); }
                } }
            else cnt[b] = 0;
            if (stable[b] && !ignore[b]) {
                held[b] += 10;
                if (held[b] >= LONG_MS && !longsent[b]) { longsent[b] = 1; post(b, E_LONG, held[b]); }
            }
        }
    }
}

uint64_t buttons_wake_mask(void) {
    uint64_t m = 0;
    for (int b = 0; b < B_COUNT; b++) if (pins[b] >= 0) m |= 1ULL << pins[b];
    return m;
}

void buttons_init(void) {
    gpio_config_t c = { .pin_bit_mask = buttons_wake_mask(), .mode = GPIO_MODE_INPUT, .pull_up_en = 1 };
    gpio_config(&c);
    xTaskCreate(task, "buttons", 3072, 0, 5, 0);
}
