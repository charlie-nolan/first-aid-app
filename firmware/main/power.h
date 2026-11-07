#pragma once
#include <stdint.h>
#include <stdbool.h>
typedef struct { int mv; int8_t pct; bool charging, reserve, critical; } battery_t;
void power_init(void);
battery_t power_battery(void);      /* pct = -1 if this board can't read the battery */
void power_mesh(bool on);
void power_mic(bool on);
bool power_woke_from_sleep(void);
/* Deep sleep. Buttons wake us; timer_s > 0 also wakes after that many seconds. Never returns. */
void power_sleep(uint32_t timer_s, bool keep_mesh);
