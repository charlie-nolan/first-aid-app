/* power.c - battery, crank, mesh/mic power switches and deep sleep.
 *
 * Crank: the crank feeds the Pololu 5 V regulator into the XIAO's charger, so
 * the firmware only sees it as the battery voltage rising. We call it
 * "charging" when the reading climbs >= 25 mV over ~30 s. Below the critical
 * level the device shows the sleep card and sleeps, waking every 5 minutes to
 * see if cranking has brought the cell back. */
#include "power.h"
#include "board.h"
#include "buttons.h"
#include "driver/gpio.h"
#include "driver/rtc_io.h"
#include "esp_sleep.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "soc/soc_caps.h"
#if PIN_BAT_ADC >= 0
#include "esp_adc/adc_oneshot.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
static adc_oneshot_unit_handle_t adc;
static adc_cali_handle_t cali;
#endif

static int hist[4], nhist;                 /* readings ~10 s apart, for the charging guess */
static int64_t last_hist_us;

static void out_pin(int pin, int level) {
    if (pin < 0) return;
    gpio_hold_dis(pin);
    gpio_config_t c = { .pin_bit_mask = 1ULL << pin, .mode = GPIO_MODE_OUTPUT };
    gpio_config(&c); gpio_set_level(pin, level);
}
static int mesh_level(bool on) {
#if CONFIG_LIB_MESH_ACTIVE_LOW
    return !on;
#else
    return on;
#endif
}

bool power_woke_from_sleep(void) {
    esp_sleep_wakeup_cause_t w = esp_sleep_get_wakeup_cause();
    return w == ESP_SLEEP_WAKEUP_EXT1 || w == ESP_SLEEP_WAKEUP_TIMER;
}

void power_init(void) {
    if (PIN_MIC_PWR >= 0) out_pin(PIN_MIC_PWR, 0);
    if (PIN_MESH_PWR >= 0) gpio_hold_dis(PIN_MESH_PWR);  /* keeps its level until power_mesh() is called */
    for (int b = 0; b < 64; b++) if (buttons_wake_mask() >> b & 1) rtc_gpio_deinit(b);
#if PIN_BAT_ADC >= 0
    adc_oneshot_unit_init_cfg_t u = { .unit_id = ADC_UNIT_1 };
    adc_oneshot_new_unit(&u, &adc);
    adc_oneshot_chan_cfg_t c = { .atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_DEFAULT };
    adc_oneshot_config_channel(adc, BAT_ADC_CH, &c);
    adc_cali_curve_fitting_config_t k = { .unit_id = ADC_UNIT_1, .chan = BAT_ADC_CH,
                                          .atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_DEFAULT };
    if (adc_cali_create_scheme_curve_fitting(&k, &cali) != ESP_OK) cali = 0;
#endif
}

/* rough LiPo resting curve, mV at 0,10,..,100 % */
static const int curve[11] = { 3300, 3500, 3600, 3680, 3730, 3780, 3830, 3900, 3980, 4080, 4180 };
static int8_t pct_of(int mv) {
    if (mv <= curve[0]) return 0;
    for (int i = 1; i < 11; i++)
        if (mv < curve[i]) return (int8_t)((i - 1) * 10 + 10 * (mv - curve[i - 1]) / (curve[i] - curve[i - 1]));
    return 100;
}

battery_t power_battery(void) {
    battery_t b = { .mv = 0, .pct = -1 };
#if PIN_BAT_ADC >= 0
    int sum = 0, n = 0, lo = 1 << 30, hi = 0;
    for (int i = 0; i < 16; i++) {
        int raw, mv;
        if (adc_oneshot_read(adc, BAT_ADC_CH, &raw) != ESP_OK) continue;
        if (!(cali && adc_cali_raw_to_voltage(cali, raw, &mv) == ESP_OK)) mv = raw * 3100 / 4095;
        sum += mv; n++;
        if (mv < lo) lo = mv;
        if (mv > hi) hi = mv;
    }
    if (!n) return b;
    b.mv = sum / n * CONFIG_LIB_BAT_DIVIDER_X100 / 100;
    /* Nothing wired (an open pin wanders) or a reading no LiPo can give: report
     * "unknown" so a bench board without the divider is never locked to the
     * emergency pages or put to sleep as "critical". */
    if (hi - lo > 150 || b.mv < 2800 || b.mv > 4500) {
        static int64_t last_log_us = -60000000;          /* say why, at most once a minute */
        int64_t t = esp_timer_get_time();
        if (t - last_log_us > 60000000) {
            ESP_LOGI("power", "battery reading ignored: pin %d..%d mV, battery %d mV (want 2800..4500, spread <= 150)", lo, hi, b.mv);
            last_log_us = t;
        }
        b.mv = 0; return b;
    }
    b.pct = pct_of(b.mv);
    int64_t now = esp_timer_get_time();
    if (!nhist || now - last_hist_us > 10 * 1000000LL) {
        if (nhist == 4) { hist[0] = hist[1]; hist[1] = hist[2]; hist[2] = hist[3]; nhist = 3; }
        hist[nhist++] = b.mv; last_hist_us = now;
    }
    b.charging = nhist >= 3 && b.mv - hist[0] >= 25;
    b.reserve = b.mv < CONFIG_LIB_BAT_RESERVE_MV;
    b.critical = b.mv < CONFIG_LIB_BAT_CRITICAL_MV;
#endif
    return b;
}

void power_mesh(bool on) { if (PIN_MESH_PWR >= 0) out_pin(PIN_MESH_PWR, mesh_level(on)); }
void power_mic(bool on) { if (PIN_MIC_PWR >= 0) out_pin(PIN_MIC_PWR, on); }

void power_sleep(uint32_t timer_s, bool keep_mesh) {
    power_mic(false);
    if (PIN_MIC_PWR >= 0) gpio_hold_en(PIN_MIC_PWR);
    if (PIN_MESH_PWR >= 0) { if (!keep_mesh) power_mesh(false); gpio_hold_en(PIN_MESH_PWR); }
#if !SOC_GPIO_SUPPORT_HOLD_SINGLE_IO_IN_DSLP
    gpio_deep_sleep_hold_en();
#endif
    uint64_t mask = buttons_wake_mask();
    for (int b = 0; b < 64; b++) if (mask >> b & 1) {       /* buttons pull to GND: keep pull-ups on while asleep */
        rtc_gpio_init(b); rtc_gpio_set_direction(b, RTC_GPIO_MODE_INPUT_ONLY);
        rtc_gpio_pullup_en(b); rtc_gpio_pulldown_dis(b);
    }
    esp_sleep_pd_config(ESP_PD_DOMAIN_RTC_PERIPH, ESP_PD_OPTION_ON);
    esp_sleep_enable_ext1_wakeup(mask, ESP_EXT1_WAKEUP_ANY_LOW);
    if (timer_s) esp_sleep_enable_timer_wakeup((uint64_t)timer_s * 1000000ULL);
    esp_deep_sleep_start();
}
