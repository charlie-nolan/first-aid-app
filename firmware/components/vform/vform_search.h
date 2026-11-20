/* vform_search.h - keyword search mode (port of pipeline/voice_router/vr/search.py at 6244a99):
 * one spoken word -> ranked cards, paged 8 at a time (4 x 2 grid). The index is built on the PC by
 * vr.search.build_index itself (tools/searchpack.py, SRCH chunk); the C only scores, ranks and pages. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define FS_PAGE 8
#define FS_MAX_HITS 48

typedef struct { const uint8_t *b; size_t len; uint32_t nkeys, nnodes; uint16_t ntrees; } fs_index_t;
typedef struct { const char *node, *title; bool life; int score; } fs_hit_t;

bool fs_open(fs_index_t *x, const void *blob, size_t len);
/* Ranked hits for a spoken word or short phrase. Returns how many (<= FS_MAX_HITS). */
int fs_search(const fs_index_t *x, const char *query, fs_hit_t *out);
/* The parity frame {"v":1,"kind":"search","query",...,"pages":[[...]]}. Returns its length. */
int fs_frame_json(const fs_index_t *x, const char *query, char *out, size_t cap);
