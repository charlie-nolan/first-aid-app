/* Host simulator: runs the firmware's ui.c on a PC and saves each screen.
 *   gcc -O2 -Wall -Wextra -I../main -I../../romkit/reader -o sim sim.c ../main/ui.c ../main/sleep_art.c ../../romkit/reader/cart.c
 *   ./sim ../library/library.bin OUTDIR [plain|plus] KEYS...
 * KEYS: down up talk back wl wr (short press), DOWN UP TALK BACK WL WR (long press),
 *       say:N[,M..] (voice result, command ids), num:1-4 / key:back|again|home (voice keys, plus), low / ok (battery reserve on/off), sleep,
 *       family:N (0 Lexend, 1 Bitter), font:N (text size 0-2).
 * Board voice (build with -DSIM_VOICE, see below): speak:<words> (a TALK clip that was heard as <words>: new
 *       problem, an answer on the form, or the search word, as main.c decides), again:<words> (heard, but
 *       rejected), and the form's picks / skip / open run the real form engine (main/vbrain.c) on the cart.
 *   gcc -O2 -DSIM_VOICE -I../main -I../../romkit/reader -I../components/vform -I$IDF_PATH/components/json/cJSON
 *       -o simv sim.c ../main/ui.c ../main/sleep_art.c ../main/vbrain.c ../../romkit/reader/cart.c ../components/vform/vform.c
 *       ../components/vform/vform_show.c ../components/vform/vform_search.c $IDF_PATH/components/json/cJSON/cJSON.c -lm
 * Writes OUTDIR/NN.pbm per step (250x122, 1 = black). */
#include "ui.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#ifdef SIM_VOICE
#include "vbrain.h"
static ui_t u;
static vs_screen_t vs; static vs_hit_t hits[VS_HITS];
static ui_act_t vspeak(const char *text, int reject) {   /* as main.c: where the clip goes */
    if (u.mode == M_SEARCH) {
        u.nhits = (int16_t)vb_search(text, reject, 51, hits, VS_HITS); snprintf(u.squery, sizeof u.squery, "%s", text);
        return ui_vsearch(&u);
    }
    const char *h1[1] = {text};
    vb_said(h1, 1, reject, 51, u.mode != M_FORM, &vs);
    return ui_vscreen(&u);
}
#endif
static uint8_t bits[UI_STRIDE * UI_H];
static ui_saved_t saved;
#ifndef SIM_VOICE
static ui_t u;
#endif
static void dump(const char *dir, int n, const char *what) {
    char p[512]; snprintf(p, sizeof p, "%s/%02d.pbm", dir, n);
    FILE *f = fopen(p, "wb"); fprintf(f, "P4\n# %s\n256 %d\n", what, UI_H); fwrite(bits, 1, sizeof bits, f); fclose(f);
}
int main(int argc, char **argv) {
    if (argc < 4) { fprintf(stderr, "usage: sim LIB.bin OUTDIR plain|plus KEYS...\n"); return 2; }
    FILE *f = fopen(argv[1], "rb"); if (!f) { perror(argv[1]); return 1; }
    fseek(f, 0, SEEK_END); long n = ftell(f); rewind(f);
    uint8_t *rom = malloc(n); if (fread(rom, 1, n, f) != (size_t)n) return 1; fclose(f);
    cart_t c; int rc = cart_open(&c, rom, (uint32_t)n, 1);
    if (rc) { fprintf(stderr, "cart_open %d\n", rc); return 1; }
    int plus = !strcmp(argv[3], "plus");
    ui_init(&u, &c, &saved);
    u.has_window_keys = u.has_voice = u.has_ir = u.has_mesh = (uint8_t)plus;
    u.has_ir = 1; u.battery_pct = plus ? 78 : -1;
    u.voice_numbers = (uint8_t)plus; u.key_min_pct = 50;   /* Plus: numbered tiles, voice keys */
    cart_fb_t fb = { bits, UI_STRIDE, UI_W, UI_H };
#ifdef SIM_VOICE
    if (vb_init(rom, (size_t)n)) { u.vs = &vs; u.hits = hits; u.has_form = 1; u.has_search = vb_has_search(); u.has_voice = 1; u.voice_numbers = 0; }
    fprintf(stderr, "board voice: form %d (%d trees), search %d, cards %d\n", vb_has_form(), vb_ntrees(), vb_has_search(), vb_has_cards());
#endif
    int k = 0;
    ui_render(&u, &fb); dump(argv[2], k++, "boot");
    for (int i = 4; i < argc; i++) {
        const char *s = argv[i]; ui_act_t a = { 0, 0, -1, 0, 0, 0, 0, 0 };
        static const char *names[] = { "talk", "back", "wl", "wr", "up", "down" };
        int b = -1, lng = isupper((unsigned char)s[0]);
        for (int j = 0; j < B_COUNT; j++) if (!strcasecmp(s, names[j])) b = j;
        if (b >= 0) {
            /* what the button driver sends: PRESS, then SHORT or LONG, then RELEASE */
            ui_act_t r[3];
            r[0] = ui_event(&u, (ui_btn_t)b, E_PRESS, 0);
            r[1] = ui_event(&u, (ui_btn_t)b, lng ? E_LONG : E_SHORT, lng ? 900 : 100);
            r[2] = ui_event(&u, (ui_btn_t)b, E_RELEASE, lng ? 900 : 100);
            for (int j = 0; j < 3; j++) {
                if (r[j].signal >= 0) a.signal = r[j].signal;
                a.light_step |= r[j].light_step; a.sleep_now |= r[j].sleep_now;
                if (r[j].voice) a.voice = r[j].voice;
                if (r[j].vf) a.vf = r[j].vf;
            }
#ifdef SIM_VOICE
            if (a.vf) { vb_op_t o; if (vb_op_of(a.vf, &u, &o) && vb_op(&o, &vs)) a = ui_vscreen(&u); }
#endif
        } else if (!strncmp(s, "say:", 4)) {
            int cmd[3]; float pr[3] = { 0.9f, 0.3f, 0.2f }; int m = 0;
            for (char *p = (char *)s + 4; *p && m < 3; ) { cmd[m++] = (int)strtol(p, &p, 10); if (*p == ',') p++; }
            if (m > 1) pr[0] = 0.3f;
            a = ui_voice(&u, cmd, pr, m);
        } else if (!strncmp(s, "num:", 4) || !strncmp(s, "key:", 4)) {
            static const char *kn[] = { "1", "2", "3", "4", "back", "again", "home" };
            int kk = -1; for (int j = 0; j < VK_COUNT; j++) if (!strcmp(s + 4, kn[j])) kk = j;
            if (kk < 0) { fprintf(stderr, "bad %s\n", s); return 2; }
            int cmd[1] = { UI_VOICE_KEY(kk) }; float pr[1] = { 0.9f };
            a = ui_voice(&u, cmd, pr, 1);
        }
#ifdef SIM_VOICE
        else if (!strncmp(s, "speak:", 6)) a = vspeak(s + 6, 0);
        else if (!strncmp(s, "again:", 6)) a = vspeak(s + 6, 90);
#endif
        else if (!strncmp(s, "family:", 7)) saved.family = (uint8_t)atoi(s + 7);
        else if (!strncmp(s, "font:", 5)) { saved.font = (uint8_t)atoi(s + 5); saved.win[saved.cur].page = 0; }
        else if (!strcmp(s, "low")) { u.low_battery = 1; u.battery_pct = 15; }
        else if (!strcmp(s, "ok")) { u.low_battery = 0; u.battery_pct = 78; }
        else if (!strcmp(s, "sleep")) { ui_render_sleep(&u, &fb); dump(argv[2], k++, s); continue; }
        if (a.signal >= 0) u.signal = (uint8_t)a.signal;
        ui_render(&u, &fb); dump(argv[2], k++, s);
        printf("%02d %-8s mode %d win %d node %d page %d sel %d font %d%s%s%s%s\n", k - 1, s, u.mode, saved.cur,
               saved.win[saved.cur].node, saved.win[saved.cur].page, saved.win[saved.cur].sel, saved.font,
               a.light_step ? " LIGHT" : "", a.signal >= 0 ? " SIGNAL" : "", a.sleep_now ? " SLEEP" : "", a.voice ? " VOICE" : "");
    }
    return 0;
}
