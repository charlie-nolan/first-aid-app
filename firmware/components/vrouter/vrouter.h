/* vrouter.h - voice router + per-tree experts (pseudo mixture of experts).
 *
 * Input: what the recognizer heard, as word ids from the cartridge lexicon (n-best).
 * Output: scores over a fixed list of classes (router: topics + reject; expert: the
 * tree's nodes + "none"). It never makes text: every class is a signed-off card.
 *
 * A model is one "VXP1" blob (pipeline/voice_router writes it). The device keeps the
 * router mapped all the time and points at one expert at a time; both are read in
 * place from memory-mapped flash, so switching expert costs nothing but a pointer.
 * Plain C, no ESP-IDF calls: it also builds on the PC for the host test. */
#pragma once
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#define VR_MAX_HYP 5
#define VR_MAX_WORDS 16
#define VR_MAX_CLASSES 64
#define VR_MAX_D 256
#define VR_MAX_HIDDEN 512

/* Agreed with the speech recognizer thread 2026-11-16. Lexicon ids < 1024, 0 = UNK,
 * 1-4 one..four, 5 yes, 6 no, 7 back, 8 again, 9 home. conf 0-100, reject 0-100. */
typedef struct {
    uint8_t n;
    struct { uint8_t len; uint16_t id[VR_MAX_WORDS]; uint8_t conf[VR_MAX_WORDS]; } h[VR_MAX_HYP];
    uint8_t reject;
} voice_hyp_t;

enum { VR_CLS_OTHER, VR_CLS_BODY, VR_CLS_MECH, VR_CLS_SYMPTOM, VR_CLS_NEG, VR_CLS_QTY };

typedef struct {
    uint16_t kind, V, B, C, d, hidden, k;
    const uint8_t *cls;                  /* [V] word class */
    const float *ws; const int8_t *w;    /* word rows */
    const float *hs; const int8_t *h;    /* hashed bigram / negation rows */
    const float *cs; const int8_t *c;    /* context rows (expert: current node) */
    const float *s1, *b1; const int8_t *w1;
    const float *s2, *b2; const int8_t *w2;
    const uint16_t *class_map;
    const char *names;                   /* k NUL-terminated names, for logs */
} vr_model_t;

/* Points m into blob (no copy). Returns false if the blob is not a valid VXP1. */
bool vr_open(vr_model_t *m, const void *blob, size_t len);

/* Scores for every class (softmax probabilities) into p[m->k]. ctx = current node, or -1. */
void vr_score(const vr_model_t *m, const voice_hyp_t *hyp, int ctx, float *p);

/* Top 3 classes by probability. */
void vr_top3(const vr_model_t *m, const float *p, int out[3]);

const char *vr_name(const vr_model_t *m, int cls);
