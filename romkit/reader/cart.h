/* cart.h - tiny reader for cartridge ROMs (library.bin). No malloc, no libc
 * beyond memcpy. The ROM is used in place: on the ESP32-S3, mmap the data
 * partition (esp_partition_mmap) and pass the pointer; reads go through the
 * flash cache, so nothing is copied to RAM. */
#ifndef CART_H
#define CART_H
#include <stdint.h>
#include <stddef.h>

#define CART_NONE 0xFFFF
enum { CART_OK = 0, CART_EMAGIC = -1, CART_ECHUNK = -2, CART_ECRC = -3 };
enum { CK_NEXT, CK_KIT, CK_IMPROVISED, CK_NOTHING, CK_YES, CK_NO };   /* choice kinds */
#define CART_NODE_EMERGENCY 1u   /* readable from the battery reserve */
#define CART_NODE_SPECIES   2u
#define CART_NODE_NO_MATCH  4u   /* show cart_string("NO_MATCH") */
#define CART_TXT_HEADING 0x01   /* at line start */
#define CART_TXT_BULLET  0x02   /* at line start */

typedef struct {               /* 16 bytes, as stored */
    uint16_t label, first_passage, first_choice;
    uint8_t  n_passages, n_choices;
    uint16_t figure, tree;
    uint8_t  flags, n_lookalikes;   /* look-alike passages follow the n_passages */
    uint16_t rsv16;
} cart_node_t;

typedef struct { uint16_t to, label; uint8_t kind; } cart_choice_t;
typedef struct { uint16_t text, citation, source; } cart_passage_t;
typedef struct { uint16_t title, root, shelf; uint8_t emergency; } cart_tree_t;
typedef struct { uint16_t w, h; uint8_t codec, pan; uint16_t caption, citation;
                 const uint8_t *data; size_t len; } cart_pic_t;

typedef struct {
    const uint8_t *base; uint32_t size;
    uint16_t home_tree, sleep_picture, n_nodes, n_texts;
    const uint8_t *syms, *huff, *tidx, *text, *pasg, *node, *npas,
                  *choi, *tree, *cmds, *srcs, *picx, *picd, *strs;
    const uint8_t *font;          /* optional FONT chunk, 0 if absent */
    const uint8_t *ntag, *tagn, *phrs;   /* optional context + voice-list chunks */
    uint16_t n_phrs;
    uint32_t build_id;
    uint16_t n_syms, n_strs, n_cmds, n_pics, n_passages, n_trees;
} cart_t;

int  cart_open(cart_t *c, const void *rom, uint32_t len, int check_crc);
const cart_node_t *cart_node(const cart_t *c, uint16_t id);   /* direct, 16-byte aligned record */
cart_passage_t cart_node_passage(const cart_t *c, const cart_node_t *n, unsigned i); /* i < n_passages + n_lookalikes */
cart_choice_t  cart_node_choice(const cart_t *c, const cart_node_t *n, unsigned i);
cart_tree_t    cart_tree(const cart_t *c, uint16_t id);
uint16_t cart_cmd_node(const cart_t *c, uint16_t cmd_id);    /* voice command -> node */
uint16_t cart_source_title(const cart_t *c, uint16_t source); /* -> text id */
uint16_t cart_string(const cart_t *c, const char *name);    /* "WAKE_911" -> text id */

/* Decode text `id`, skipping the first `skip` bytes (for paging), writing at
 * most cap-1 bytes plus NUL. Returns the full length of the text. */
size_t cart_text(const cart_t *c, uint16_t id, size_t skip, char *out, size_t cap);

/* Pictures: rows are delivered one at a time, MSB first, 1 = black.
 * row_cb(user, y, row, bytes_per_row). Needs 2 KB of stack for codec 2. */
int  cart_pic(const cart_t *c, uint16_t id, cart_pic_t *p);
typedef void (*cart_row_cb)(void *user, int y, const uint8_t *row, int bpr);
int  cart_pic_decode(const cart_pic_t *p, cart_row_cb cb, void *user);

/* ---- fonts and page layout (CrossInk-style: fixed sizes bundled in the ROM) ----
 * 1-bit framebuffer, 1 = black, rows MSB first, `stride` bytes per row. */
typedef struct { uint8_t *bits; int stride, w, h; } cart_fb_t;
/* A "style" is family * cart_font_sizes() + size: with the default ROM, 0-2 = Lexend
 * small/medium/large and 3-5 = Bitter small/medium/large. */
unsigned cart_font_sizes(const cart_t *c);                       /* 3: small, medium, large */
unsigned cart_font_families(const cart_t *c);                    /* 2: Lexend, Bitter */
const char *cart_font_family_name(const cart_t *c, unsigned family);
int  cart_font_line_height(const cart_t *c, unsigned style);
/* Draw a plain string with its baseline at y; returns its width in pixels. fb may be 0 to measure. */
int  cart_draw_str(const cart_t *c, unsigned style, int bold, cart_fb_t *fb, int x, int y, const char *s);
/* Word-wrap text `id` at `size` into the box (x, y, w, h) and draw page `page` (0-based) into fb.
 * fb = 0 only counts. Headings draw bold, bullets hang-indent. `scratch` holds the decoded text.
 * Returns the number of pages. */
int  cart_page(const cart_t *c, uint16_t id, unsigned style, int page, cart_fb_t *fb,
               int x, int y, int w, int h, char *scratch, size_t scratch_len);

/* ---- context: state / season / activity (optional NTAG, TAGN, PHRS chunks) ----
 * Tags and contexts are 96 bits: region bits 0-63 (default: 50 US states + DC by postal
 * code, alphabetical), season 64-67, activity 68-75. Untagged content is global and shows
 * in every state. The firmware keeps the chosen context (in NVS) until the person changes
 * it; an empty dimension in the context means "show all". Within one dimension a node
 * matches if it has no tags there, the context has none chosen there, or they share a bit.
 * Emergency nodes are always visible. */
#define CART_PHRASE_ALWAYS 1u   /* in every voice list (911, emergency entry) */
#define CART_PHRASE_ENTRY  2u   /* opens a section (points at a tree root) */
#define CART_VOICE_MAX   200    /* MultiNet: up to 200 commands per list */
typedef struct { uint32_t w[3]; } cart_tags_t;
static inline void cart_tags_set(cart_tags_t *t, unsigned bit) { if (bit < 96) t->w[bit >> 5] |= 1u << (bit & 31); }
typedef struct { uint16_t text, node; uint8_t flags; } cart_phrase_t;
cart_tags_t cart_node_tags(const cart_t *c, uint16_t node);
int      cart_node_visible(const cart_t *c, uint16_t node, const cart_tags_t *ctx);
unsigned cart_ctx_dims(const cart_t *c);
/* dimension `dim`: its name text ("region"), first bit and number of bits */
int      cart_ctx_dim(const cart_t *c, unsigned dim, uint16_t *name_text, unsigned *shift, unsigned *width);
/* Clear one dimension of a context (e.g. before picking a new state). */
void     cart_ctx_clear_dim(const cart_t *c, cart_tags_t *ctx, unsigned dim);
uint16_t cart_ctx_label(const cart_t *c, unsigned bit);       /* "Washington", or CART_NONE */
uint16_t cart_phrase_count(const cart_t *c);                  /* = number of ESP-SR command ids */
cart_phrase_t cart_phrase(const cart_t *c, uint16_t cmd_id);
/* Voice list for section `tree` (CART_NONE = home), filtered by ctx, filled as far as `cap`:
 * always phrases, then every section's entry phrases (so you can always jump), then this
 * section's own phrases, then every other visible phrase. Identical wording is kept once.
 * Writes ESP-SR command ids to ids[]; returns the count. Load it with esp_mn_commands_clear(),
 * esp_mn_commands_add(id, text) per id, esp_mn_commands_update(). */
int      cart_voice_list(const cart_t *c, uint16_t tree, const cart_tags_t *ctx, uint16_t *ids, int cap);
#endif
