/* app.h - the one queue every task posts to; app_main handles them in order. */
#pragma once
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "ui.h"
typedef enum { MSG_BUTTON, MSG_VOICE } msg_type_t;
typedef struct {
    msg_type_t type;
    uint8_t btn, kind;           /* MSG_BUTTON: ui_btn_t, ui_evkind_t */
    int16_t held_ms;
    int8_t n;                    /* MSG_VOICE: number of results (0 = nothing heard) */
    int cmd[3]; float prob[3];
} app_msg_t;
extern QueueHandle_t app_q;
