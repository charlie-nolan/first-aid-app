/* epd.h - Waveshare 2.13" V4 (SSD1680, 122 x 250) over SPI. */
#pragma once
#include <stdint.h>
#include <stdbool.h>
void epd_init(void);                       /* SPI + pins; wakes and resets the panel */
/* Send a landscape 250 x 122 framebuffer (32 bytes/row, 1 = black).
 * full: full refresh (~2 s, flashes, clears ghosting); else partial (~0.3 s). */
void epd_show(const uint8_t *fb, bool full);
void epd_sleep(void);                      /* panel deep sleep; image stays */
