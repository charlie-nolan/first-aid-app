/* epd.h - Waveshare 2.13" V4 (SSD1680, 122 x 250) over SPI. */
#pragma once
#include <stdint.h>
#include <stdbool.h>
void epd_init(void);                       /* SPI + pins; wakes and resets the panel */
/* Send a landscape 250 x 122 framebuffer (32 bytes/row, 1 = black).
 * EPD_PARTIAL: quick page turn (~0.6 s, no flash, leaves faint ghosts).
 * EPD_CLEAN:   one gentle flash (~1.5 s) that clears ghosts; the periodic clean-up.
 * EPD_FULL:    the panel's full waveform (~2.3 s, several flashes); deepest clean,
 *              used after power-up and for the two-button refresh. */
enum { EPD_PARTIAL, EPD_CLEAN, EPD_FULL };
void epd_show(const uint8_t *fb, int mode);
void epd_sleep(void);                      /* panel deep sleep; image stays */
