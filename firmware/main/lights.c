/* lights.c - front light, flashlight, SOS (Morse ...---...) on the white or the IR bead,
 * and the CPR pace light (a short flash on the front light for each compression).
 * Only one bead is ever lit at a time (the 300 mAh cell can give ~0.6 A bursts).
 * When the white bead shares the front-light pin (as wired in the hardware doc),
 * a running signal takes over that pin and the front light waits. */
#include "lights.h"
#include "board.h"
#include "ui.h"
#include "driver/ledc.h"
#include "driver/gpio.h"
#include "esp_timer.h"

#define CH_FRONT LEDC_CHANNEL_0
#define CH_WHITE LEDC_CHANNEL_1
#define SHARED (PIN_WHITE == PIN_FRONT)
#define TICK_MS 50
/* SOS in Morse, one char per 200 ms unit: dot 1, dash 3, gaps 1 / 3 / 7 */
static const char SOS[] = "1010100011101110111000101010000000";
/* CPR pace: ATP 4-02.11 (2026) para 6-45, "Perform compressions at a rate of 100 to
 * 120 compressions per minute. This equates to about two compressions per second."
 * We flash at the middle of that range. The timer runs at a quarter beat, so the
 * flash is one quarter (~136 ms) on and three quarters off, with no drift. */
#define CPR_PER_MIN   110
#define CPR_QUARTER_US (60000000LL / CPR_PER_MIN / 4)
static uint8_t front, sig;
static uint32_t tick;
static esp_timer_handle_t timer;

static void duty(ledc_channel_t ch, uint32_t d) { ledc_set_duty(LEDC_LOW_SPEED_MODE, ch, d); ledc_update_duty(LEDC_LOW_SPEED_MODE, ch); }
static uint32_t front_duty(void) { return front == 2 ? DUTY_FRONT_BRIGHT : front == 1 ? DUTY_FRONT_DIM : 0; }

static void apply(int white_on, int ir_on, uint32_t white_duty) {
    if (PIN_IR >= 0) gpio_set_level(PIN_IR, ir_on && !white_on);
    uint32_t w = white_on ? white_duty : 0;
    if (SHARED) duty(CH_FRONT, sig == SIG_OFF ? front_duty() : w);
    else { duty(CH_WHITE, w); duty(CH_FRONT, front_duty()); }
}

static void on_tick(void *arg) {
    tick++;
    switch (sig) {
    case SIG_FLASHLIGHT: apply(1, 0, DUTY_FLASHLIGHT); break;
    case SIG_SOS_WHITE:  apply(SOS[(tick * TICK_MS / 200) % (sizeof SOS - 1)] == '1', 0, DUTY_STROBE); break;
    case SIG_IR:         apply(0, SOS[(tick * TICK_MS / 200) % (sizeof SOS - 1)] == '1', 0); break;   /* SOS too: reads as distress on night vision */
    case SIG_CPR:                                       /* front light: pace flash, else the reader's own level */
        duty(CH_FRONT, (tick & 3) == 1 ? DUTY_CPR_PACE : front_duty());
        if (!SHARED) duty(CH_WHITE, 0);
        if (PIN_IR >= 0) gpio_set_level(PIN_IR, 0);
        break;
    default:             apply(0, 0, 0); break;
    }
}

static void chan(ledc_channel_t ch, int pin) {
    ledc_channel_config_t c = { .gpio_num = pin, .speed_mode = LEDC_LOW_SPEED_MODE, .channel = ch,
                                .timer_sel = LEDC_TIMER_0, .duty = 0, .hpoint = 0 };
    ledc_channel_config(&c);
}

void lights_init(void) {
    if (PIN_IR >= 0) gpio_hold_dis(PIN_IR);
    if (PIN_FRONT >= 0) gpio_hold_dis(PIN_FRONT);
    if (PIN_WHITE >= 0) gpio_hold_dis(PIN_WHITE);
    ledc_timer_config_t t = { .speed_mode = LEDC_LOW_SPEED_MODE, .duty_resolution = LEDC_TIMER_10_BIT,
                              .timer_num = LEDC_TIMER_0, .freq_hz = 5000, .clk_cfg = LEDC_AUTO_CLK };
    ledc_timer_config(&t);
    chan(CH_FRONT, PIN_FRONT);
    if (!SHARED) chan(CH_WHITE, PIN_WHITE);
    if (PIN_IR >= 0) {
        gpio_config_t c = { .pin_bit_mask = 1ULL << PIN_IR, .mode = GPIO_MODE_OUTPUT, .pull_down_en = 1 };
        gpio_config(&c); gpio_set_level(PIN_IR, 0);
    }
    esp_timer_create_args_t a = { .callback = on_tick, .name = "lights" };
    esp_timer_create(&a, &timer);
}

void lights_front(uint8_t level) { front = level % 3; if (sig != SIG_CPR) on_tick(0); }   /* CPR: next beat picks it up, pace stays even */
uint8_t lights_signal_active(void) { return sig != SIG_OFF; }

void lights_signal(uint8_t s) {
    sig = s; tick = 0;
    esp_timer_stop(timer);
    if (sig == SIG_CPR) esp_timer_start_periodic(timer, CPR_QUARTER_US);   /* first flash on the first tick */
    else if (sig != SIG_OFF) esp_timer_start_periodic(timer, TICK_MS * 1000);
    on_tick(0);
}

void lights_off_for_sleep(void) {
    sig = SIG_OFF; esp_timer_stop(timer);
    ledc_stop(LEDC_LOW_SPEED_MODE, CH_FRONT, 0);
    if (!SHARED) ledc_stop(LEDC_LOW_SPEED_MODE, CH_WHITE, 0);
    const int pins[] = { PIN_FRONT, PIN_WHITE, PIN_IR };
    for (int i = 0; i < 3; i++) if (pins[i] >= 0) {     /* MOSFET gates must not float while asleep */
        gpio_set_direction(pins[i], GPIO_MODE_OUTPUT); gpio_set_level(pins[i], 0); gpio_hold_en(pins[i]);
    }
}
