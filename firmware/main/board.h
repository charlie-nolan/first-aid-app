/* board.h - pin maps. Pick the board in menuconfig (Librarian > Board), or
 * build with sdkconfig.plain / sdkconfig.plus (see README). PIN_NONE = not fitted.
 *
 * XIAO pad -> GPIO: D0=1 D1=2 D2=3 D3=4 D4=5 D5=6 D6=43 D7=44 D8=7 D9=8 D10=9
 * Plus extra pads:  D11=38 D12=39 D13=40 D14=41 D15=42 D17=13 D18=12 D19=11, ADC_BAT=10 */
#pragma once
#include "sdkconfig.h"
#define PIN_NONE (-1)

/* e-ink: same pads on both boards (Waveshare 2.13" V4 HAT, SSD1680) */
#define PIN_EPD_SCK   7    /* D8  -> HAT CLK  (Pi pin 23) */
#define PIN_EPD_MOSI  9    /* D10 -> HAT DIN  (Pi pin 19) */
#define PIN_EPD_CS    8    /* D9  -> HAT CS   (Pi pin 24) */
#define PIN_EPD_DC    43   /* D6  -> HAT DC   (Pi pin 22) */
#define PIN_EPD_RST   44   /* D7  -> HAT RST  (Pi pin 11) */

#if CONFIG_LIB_BOARD_PLUS
#define BOARD_NAME "XIAO ESP32-S3 Plus"
#define PIN_EPD_BUSY  13   /* D17 */
#define PIN_BTN_TALK  1    /* D0, right end, big red */
#define PIN_BTN_BACK  2    /* D1, left end */
#define PIN_BTN_WIN_L 3    /* D2, top left */
#define PIN_BTN_WIN_R 4    /* D3, top right */
#define PIN_BTN_UP    5    /* D4, bottom left */
#define PIN_BTN_DOWN  6    /* D5, bottom right */
#define PIN_FRONT     11   /* D19, warm-white edge light (PWM) */
#define PIN_WHITE     CONFIG_LIB_PLUS_WHITE_GPIO   /* D18 = GPIO12: 3 W white bead MOSFET (flashlight, SOS) */
#define PIN_IR        38   /* D11, 850 nm bead MOSFET */
#define PIN_MESH_PWR  40   /* D13, Meshtastic board load switch */
#define PIN_MIC_PWR   39   /* D12 */
#define PIN_MIC_DATA  41   /* D14 */
#define PIN_MIC_CLK   42   /* D15 */
#define PIN_BAT_ADC   10   /* ADC1 channel 9 */
#define HAS_WINDOW_KEYS 1
#else
/* Plain XIAO ESP32-S3 bench build. It has only D0..D10: five go to the e-ink,
 * BUSY takes D3, so there is room for three buttons and two LED outputs. */
#define BOARD_NAME "XIAO ESP32-S3 (plain, bench)"
#define PIN_EPD_BUSY  4    /* D3  -> HAT BUSY (Pi pin 18) */
#define PIN_BTN_TALK  1    /* D0 */
#define PIN_BTN_BACK  2    /* D1 */
#define PIN_BTN_DOWN  3    /* D2 */
#define PIN_BTN_WIN_L PIN_NONE
#define PIN_BTN_WIN_R PIN_NONE
#define PIN_BTN_UP    PIN_NONE
#define PIN_FRONT     5    /* D4: white (front light + flashlight + SOS share it, as on the Plus) */
#define PIN_WHITE     5
#define PIN_IR        6    /* D5 */
#define PIN_MESH_PWR  PIN_NONE
#define PIN_MIC_PWR   PIN_NONE
#define PIN_MIC_DATA  PIN_NONE
#define PIN_MIC_CLK   PIN_NONE
#define PIN_BAT_ADC   PIN_NONE
#define HAS_WINDOW_KEYS 0
#endif

/* Light levels, 10-bit PWM duty (0..1023). Tune on the bench. */
#define DUTY_FRONT_DIM     20
#define DUTY_FRONT_BRIGHT  120
#define DUTY_FLASHLIGHT    400
#define DUTY_STROBE        1023
