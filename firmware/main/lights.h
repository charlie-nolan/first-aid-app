#pragma once
#include <stdint.h>
void lights_init(void);
void lights_front(uint8_t level);      /* 0 off, 1 dim, 2 bright */
void lights_signal(uint8_t sig);       /* ui_signal_t: off, flashlight, SOS white, SOS IR */
uint8_t lights_signal_active(void);
void lights_off_for_sleep(void);       /* everything off and held low through deep sleep */
