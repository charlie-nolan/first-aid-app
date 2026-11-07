#pragma once
#include <stdint.h>
void buttons_init(void);             /* starts a 10 ms polling task that posts MSG_BUTTON */
uint64_t buttons_wake_mask(void);    /* RTC GPIO mask for ext1 wake */
