/* vform.h - fill-in-the-blanks intake: the C port of vr/intake.py + vr/form.py (+ vr.live.keep_trees).
 *
 * The first sentence fills every blank it can in the 2-3 likely trees; one screen then lists the
 * blanks still open (a "which is it" field while trees compete); each answer fills only its own
 * field; when the blanks lead to one card, that signed-off, cited card opens. Nothing is generated:
 * every question is a tree card, every answer one of its tiles, every result a card.
 *
 * Safety rules kept from the Python (the reference, ported line for line):
 *  - a blank fills only from words actually heard; a negated or hedged word fills nothing;
 *  - lower n-best guesses may only ADD a life-threat answer the top guess missed;
 *  - snapping a near word ("ankel") needs 4+ letters, score >= 0.80, margin 0.10, never a life blank;
 *  - a life card opens only when a life rule fires.
 *
 * Data: one "FPK1" blob (tools/formpack.py) with every tree's cards, tiles, questions and slot
 * words, read in place (memory-mapped flash on the device). Plain C, no ESP-IDF calls, so the
 * same code runs in the host parity test (host_test.c). Python reference: claude/project-thread-ypggtj
 * at b2f8eb3 (pipeline/voice_router/vr). */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define FP_NONE       0xFFFFu
#define FP_MAX_TREES  64
#define FP_MAX_NODES  512
#define FP_MAX_FILLED 24      /* blanks one candidate can hold */
#define FP_MAX_CANDS  8       /* trees kept at once (keep_trees keeps 3) */
#define FP_MAX_HYPS   5
#define FP_MAX_TOK    40      /* words per sentence looked at */
#define FP_WLEN       28      /* longest word kept (longer words are cut) */
#define FP_HEARD      160
#define FP_MAX_FIELDS 16
#define FP_MAX_RANKED 40
#define FP_TOPIC      0xFFFEu /* field key of the "Which is it?" field */

typedef uint16_t fp_str;      /* string id in the pack; FP_NONE = missing */
typedef int32_t fp_card;      /* >= 0: node index in its tree; < 0: another tree's card, -1 - string id */
#define FP_EXT(sid)   (-1 - (int32_t)(sid))
#define FP_EXT_SID(c) ((fp_str)(-1 - (c)))

typedef struct {
    const uint8_t *b;
    size_t len;
    uint16_t ntrees;
    uint32_t nstr;
} fp_pack_t;

typedef struct {
    const uint8_t *rec, *nodes, *slots, *rules, *implies, *lifew, *menu;
    fp_str name, title;
    uint16_t root, nnodes, nslots, nrules, nimplies, nlifew;
} fp_tree_t;

typedef struct { uint8_t n; fp_str slot[FP_MAX_FILLED], val[FP_MAX_FILLED]; } fp_filled_t;

typedef struct {
    uint8_t tree;             /* index into the session's trees */
    double p;                 /* router (keep_trees) belief */
    fp_filled_t filled;
    char heard[FP_HEARD];     /* the first sentence as heard (ranking uses its words) */
    fp_card card;             /* where this tree's walk stands */
    fp_str asking;            /* the blank that card asks, FP_NONE = a plain card */
} fp_cand_t;

typedef struct {              /* one row of the form */
    fp_str key;               /* slot name, or FP_TOPIC */
    fp_str question;          /* card label (FP_NONE for the topic field: "Which is it?") */
    uint8_t tree;             /* the tree whose card the choices come from (first one to ask it) */
    uint16_t node;            /* that card (its tiles are the choices) */
    uint32_t trees;           /* bit per candidate index this field belongs to */
    fp_str when_slot, when_val; /* shows after that answer; FP_NONE = asked now */
} fp_field_t;

typedef struct { uint8_t tree; fp_card card; } fp_ref_t;

enum { FP_FORM, FP_OPEN, FP_SKIP, FP_TABS };
typedef struct { int kind; uint8_t tree; fp_card card; } fp_result_t;

typedef struct fp_rank_ws fp_rank_ws_t;

typedef struct {
    fp_pack_t pk;
    fp_tree_t trees[FP_MAX_TREES];
    int ntrees;
    fp_cand_t cands[FP_MAX_CANDS];
    int ncands;
    fp_rank_ws_t *ws;         /* ranked_cards work space (heap; PSRAM on the device) */
    uint32_t rank_overflow;   /* times the ranking queue was full (should stay 0) */
} fp_sess_t;

/* ---- pack ---- */
bool fp_open(fp_pack_t *pk, const void *blob, size_t len);
const char *fp_s(const fp_pack_t *pk, fp_str id);              /* NULL for FP_NONE */
int fp_find_str(const fp_pack_t *pk, const char *s);           /* string id or -1 */

/* ---- session ---- */
bool fp_sess_init(fp_sess_t *s, const void *blob, size_t len); /* allocates the work space */
void fp_sess_free(fp_sess_t *s);
void fp_reset(fp_sess_t *s);                                   /* new problem */
int fp_tree_by_name(const fp_sess_t *s, const char *name);
int fp_node_by_key(const fp_sess_t *s, int tree, const char *key);

/* vr.live.keep_trees: trees ranked by the blanks the sentence fills (life blanks count 2), top k
 * become the candidates with p = share of the score. Returns how many. */
int fp_keep_trees(fp_sess_t *s, const char *const *hyps, int nhyps, int k);
/* vr.form.first_sentence. multi = TALK + WIN_R ("several problems"). */
fp_result_t fp_first_sentence(fp_sess_t *s, const char *const *hyps, int nhyps, bool multi);
/* vr.live.rescue: call before fp_answer on every later turn; true = it handled the words (*r). */
bool fp_rescue(fp_sess_t *s, const char *const *hyps, int nhyps, fp_result_t *r);
/* vr.form.answer: one spoken answer to the screen. FP_SKIP: call fp_ranked_cards(s, 12, ..). */
fp_result_t fp_answer(fp_sess_t *s, const char *text);
/* vr.form.pick: a tapped choice. value NULL + to = a plain tile (menu choice) going to card `to`. */
fp_result_t fp_pick(fp_sess_t *s, const char *key, const char *value, const char *to);
/* vr.form.open_fields: what the screen shows now. Returns how many (<= max). */
int fp_open_fields(fp_sess_t *s, fp_field_t *out, int max);
/* vr.form.ranked_cards / skip: cards the blanks still allow, best first. */
int fp_ranked_cards(fp_sess_t *s, int limit, fp_ref_t *out, int max);
/* vr.form.split_tabs: tab i holds tabs[i].n candidate indices, in the Python's order. Returns the number of tabs. */
typedef struct { uint8_t n, c[FP_MAX_CANDS]; } fp_tab_t;
int fp_split_tabs(const fp_sess_t *s, fp_tab_t *tabs, int max);
/* Keep only the candidates of one tab (vr bridge: cands = split_tabs(cands)[0]). */
void fp_keep_tab(fp_sess_t *s, const fp_tab_t *tab);
/* vr.form.most_telling: the open blank to put first, or FP_NONE. */
fp_str fp_most_telling(fp_sess_t *s);

/* ---- tree access for the screen ---- */
typedef struct { fp_str label, value, to; int16_t to_node; } fp_tile_t;
int fp_tiles(const fp_sess_t *s, int tree, int node, fp_tile_t *out, int max);
fp_str fp_node_key(const fp_sess_t *s, int tree, int node);
fp_str fp_node_label(const fp_sess_t *s, int tree, int node);
int fp_node_cites(const fp_sess_t *s, int tree, int node, fp_str *out, int max);
/* A card to show: a card of another tree ("wfa-cpr/cpr") resolves to that tree. Returns false
 * when it names a tree not in the pack (the title is then the bare card name). */
bool fp_resolve(const fp_sess_t *s, int tree, fp_card card, int *otree, int *onode);

/* ---- text (exposed for the host test) ---- */
typedef struct { int n; char w[FP_MAX_TOK][FP_WLEN]; } fp_toks_t;
void fp_tokenize(const char *text, fp_toks_t *t);               /* vr.intake.tokenize */
void fp_stem(const char *w, bool verbs, char *out);             /* vr.intake._stem */
double fp_ratio(const char *a, const char *b);                  /* difflib.SequenceMatcher.ratio */
void fp_fill(const fp_sess_t *s, int tree, const char *text, fp_filled_t *out);   /* vr.intake._fill */
