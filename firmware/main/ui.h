/* ui.h - screens, navigation and the 5 reading windows.
 *
 * Pure C on top of the romkit reader: no ESP-IDF calls, so the same file runs
 * in the host simulator (sim/). The firmware feeds it button events and voice
 * results; it draws into a 1-bit framebuffer and returns what the hardware
 * should do next (redraw, lights, sleep, voice). */
#ifndef LIBRARIAN_UI_H
#define LIBRARIAN_UI_H
#include <stdint.h>
#include "cart.h"

#define UI_W 250            /* landscape, 2.13" panel */
#define UI_H 122
#define UI_STRIDE 32        /* bytes per framebuffer row (256 px) */
extern const uint8_t ui_sleep_art[];   /* sleep_art.c: built-in sleep picture */
#define UI_WINDOWS 5
#define UI_HIST 8
#define UI_SAVED_MAGIC 0x4C494232u   /* "LIB2" */
#define UI_LAST_TREES 32               /* manuals whose reading position is remembered */

typedef enum { B_TALK, B_BACK, B_WIN_L, B_WIN_R, B_UP, B_DOWN, B_COUNT } ui_btn_t;
typedef enum { E_PRESS, E_SHORT, E_LONG, E_RELEASE } ui_evkind_t;
typedef enum { SIG_OFF, SIG_FLASHLIGHT, SIG_SOS_WHITE, SIG_IR, SIG_CPR } ui_signal_t;   /* SIG_CPR: compression pace on the front light */
typedef enum { M_WAKE, M_READ, M_LIBRARY, M_MENU, M_PICK, M_MSG, M_CTX_DIMS, M_CTX_VALUES, M_HOME } ui_mode_t;
enum { RD_NONE, RD_PARTIAL, RD_FULL };
enum { VO_NONE, VO_START, VO_STOP, VO_CANCEL };

/* One reading window. Lives in RTC memory so it survives deep sleep. */
typedef struct {
    uint16_t node;
    uint8_t page, sel;              /* page within the node, highlighted choice */
    uint8_t nhist, pad;
    uint16_t hist[UI_HIST];         /* nodes to go BACK to, newest last */
} ui_window_t;

typedef struct {
    uint32_t magic, build_id;       /* reset if the library changes */
    ui_window_t win[UI_WINDOWS];
    uint8_t cur;                    /* current window */
    uint8_t font;                   /* 0 small, 1 medium, 2 large */
    uint8_t light;                  /* front light 0 off, 1 dim, 2 bright */
    uint8_t mesh_on;
    uint8_t family;                 /* font family (Lexend, Bitter, ...) */
    uint8_t clean;                  /* page turns between screen clean-ups; 0 = build default */
    uint8_t pad2[2];
    cart_tags_t ctx;                /* sticky context: state, season, activity (also kept in NVS) */
    uint16_t last[UI_LAST_TREES];   /* reference shelf: last section read in each manual (0 = none) */
} ui_saved_t;

typedef struct {
    uint8_t redraw;                 /* RD_* */
    uint8_t light_step;             /* 1: step the front light (off, dim, bright) */
    int8_t  signal;                 /* -1 no change, else ui_signal_t */
    uint8_t voice;                  /* VO_* */
    uint8_t mesh;                   /* 1: apply ui->saved->mesh_on */
    uint8_t sleep_now;
    uint8_t ctx_changed;            /* 1: save ui->s->ctx to NVS, reload voice list */
} ui_act_t;

typedef struct {
    /* what this board has (set by main from board.h) */
    uint8_t has_window_keys, has_voice, has_ir, has_mesh;
    /* live status (set by main) */
    int8_t battery_pct;             /* -1 = unknown (plain S3 has no battery pin) */
    uint8_t charging, low_battery, signal;

    const cart_t *c;
    ui_saved_t *s;
    ui_mode_t mode, back_mode;
    uint8_t list_sel, listening, talk_armed, ctx_dim;
    uint8_t voice_numbers;          /* tiles show 1-4 and voice keys are handled (set by main) */
    uint8_t key_min_pct;            /* voice keys below this confidence (%) are asked again */
    uint8_t menu_cpr;               /* menu opened from the CPR tree: pace light listed first */
    uint8_t lib_folder;             /* Library: 0xFF = top level, else the first tree of the open folder */
    uint8_t home_lvl, home_cat, home_grp, home_page;   /* Home tiles: 0 Injury/Survival, 1 folders, 2 topics */
    uint16_t picks[3]; uint8_t npicks;
    const char *msg;
    char scratch[4096];
} ui_t;

void ui_init(ui_t *u, const cart_t *c, ui_saved_t *s);   /* resets s if magic/build_id don't match */
void ui_render(ui_t *u, cart_fb_t *fb);                   /* clears and draws the current screen */
void ui_render_sleep(ui_t *u, cart_fb_t *fb);             /* the picture left on the panel while asleep */
ui_act_t ui_event(ui_t *u, ui_btn_t b, ui_evkind_t k, int held_ms);
/* Voice keys: the small word list used inside a topic. Answer tiles are numbered
 * 1-4 in reading order (TL, TR, BL, BR = the corner buttons), so saying a number is
 * the same as pressing that tile's button. ui_voice gets keys as command ids below
 * -100, so they never collide with the cartridge's phrase ids (0 and up). */
enum { VK_1, VK_2, VK_3, VK_4, VK_BACK, VK_AGAIN, VK_HOME, VK_COUNT };
#define UI_VOICE_KEY(k)     (-100 - (k))
#define UI_VOICE_IS_KEY(c)  ((c) <= -100 && (c) > -100 - VK_COUNT)
#define UI_VOICE_KEY_OF(c)  (-100 - (c))
#define UI_VOICE_KEY_NAME(k) ((const char *[]){ "1", "2", "3", "4", "back", "again", "home" }[(k)])
/* Voice result: ESP-SR command ids, best first, with probabilities. n = 0: nothing heard. */
ui_act_t ui_voice(ui_t *u, const int *cmd, const float *prob, int n);
#endif
