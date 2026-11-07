# Cartridge ROM format, v1 (`library.bin`)

The ROM is immutable and read in place. On the ESP32-S3 it sits in its own data
partition, mapped into the address space with `esp_partition_mmap()`, so every
table is read through the flash cache and nothing is copied to RAM. The reader
(`reader/cart.c`) needs no malloc and under 5 KB of stack (picture decode is the
largest user).

All integers are little-endian. Every chunk starts on a 4-byte boundary.
Ids are dense `u16` indexes; `0xFFFF` means none.

## Header (28 bytes) and chunk directory

| off | type | field |
|---|---|---|
| 0 | char[4] | magic `CRT1` |
| 4 | u16 | format version (1) |
| 6 | u16 | number of chunks |
| 8 | u32 | total size |
| 12 | u32 | CRC-32 of bytes 16..end |
| 16 | u16 | home tree |
| 18 | u16 | sleep picture |
| 20 | u16 | node count |
| 22 | u16 | text count |
| 24 | u32 | build id (first 8 hex digits of the builder's build_id) |
| 28 | {char tag[4], u32 offset, u32 size} x n | chunk directory |

Readers look chunks up by tag and ignore tags they don't know, so new chunk
types can be added without breaking old readers.

## Text store: `SYMS`, `HUFF`, `TIDX`, `TEXT`

All strings (passages, citations, labels, captions, UI strings) live in one
store and are addressed by text id. Identical strings are stored once.

Compression is Re-Pair (repeated byte-pair grammar) followed by canonical
Huffman, shared by the whole ROM, with each text decodable on its own.

- `SYMS`: 3 bytes per symbol, two 12-bit fields `a` (low) and `b` (high).
  `a == 0xFFF` means a character with code `b`. Otherwise the symbol expands to
  symbol `a` followed by symbol `b`. At most 4095 symbols; expansion depth is
  checked at build time to be at most 64 (the reader's stack).
- `HUFF`: `u16 count[16]`, the number of Huffman codes of each length 0..15.
  Symbols are numbered in canonical code order, so the decoded Huffman index is
  the symbol id. No separate symbol list is stored.
- `TIDX`: `u32 bit_offset[n_texts + 1]` into `TEXT`.
- `TEXT`: one MSB-first bit stream.

Device control bytes inside text: `\n` new line, `\x01` at line start = heading,
`\x02` at line start = bullet. The packer converts the builder's `## ` and `- `
line prefixes into these.

## Content tables

- `PASG` (6 B each): `u16 text, u16 citation_text, u16 source`.
- `NODE` (16 B each):
  `u16 label_text, u16 first_npas, u16 first_choice, u8 n_passages, u8 n_choices,
  u16 figure, u16 tree, u8 flags, u8 n_lookalikes, u16 reserved`.
  Flags: 1 = emergency (readable from the battery reserve), 2 = species page,
  4 = no-match leaf (show the `NO_MATCH` string).
- `NPAS`: `u16` passage indexes. A node's passages are
  `NPAS[first_npas .. first_npas + n_passages + n_lookalikes)`, look-alikes last.
- `CHOI` (6 B each): `u16 to_node, u16 label_text, u8 kind, u8 pad`.
  Kind: 0 next, 1 kit, 2 improvised, 3 nothing, 4 yes, 5 no.
- `TREE` (8 B each): `u16 title_text, u16 root_node, u16 shelf_text, u8 emergency, u8 pad`.
- `CMDS`: `u16 node[n_cmds]`, indexed by ESP-SR command id.
- `SRCS`: `u16 title_text[n_sources]`.
- `STRS` (4 B each): `u16 name_text, u16 value_text` for the fixed UI strings
  (`WAKE_911`, `DISCLAIMER`, `NO_MATCH`, `PLANT_WARNING`), sorted by name.

## Pictures: `PICX`, `PICD`

`PICX` is `u32 offset[n_pics + 1]` into `PICD`. Each picture is a 10-byte header
`u16 w, u16 h, u8 codec, u8 pan, u16 caption_text, u16 citation_text`, then data,
padded to 4 bytes. Pixels are 1 = black, rows MSB-first, padded to whole bytes.
The packer picks the smallest codec per picture:

- 0: raw rows.
- 1: PackBits over the raw row bytes.
- 2: context model. Each pixel is coded with LZMA's adaptive binary range coder
  (11-bit probabilities, shift 5), using a 10-pixel context: 3 pixels of row y-2
  (x-1..x+1), 5 of row y-1 (x-2..x+2) and 2 of row y (x-2, x-1); pixels outside
  the image are white. 1024 probabilities start at 1024. Rows decode one at a
  time, so pan pictures up to 768 px wide need only three line buffers.

## Reader API (`reader/cart.h`)

`cart_open`, `cart_node`, `cart_node_passage`, `cart_node_choice`, `cart_tree`,
`cart_cmd_node`, `cart_source_title`, `cart_string`, `cart_text` (with a `skip`
argument, for paging), `cart_pic`, `cart_pic_decode` (row callback).
`reader/cartcli.c` is a host CLI around it for the builder's test mode:
`cartcli LIB.bin cmd N` prints the node id.

## Fonts: `FONT` (optional, revised 2026-11-06)

CrossInk-style bundled fonts: 2 families (Lexend Deca, Bitter) x 3 sizes (10, 12,
15 px, Charlie's pick) x regular + bold, about 62 KB. Rasterised like CrossInk's fontconvert.py:
FreeType's hinted anti-aliased render (native hinting; the auto-hinter broke
Bitter's spacing at 10 px), with coverage >= 64/255 counted as ink so thin stems
survive at 10 px (chosen from a 32-96 sweep). Advances are FreeType's unhinted linear advance and
kerning comes from the font's GPOS/kern tables, both in 1/16 px; the reader
accumulates the pen in 1/16 px and snaps each glyph origin to the nearest pixel,
so spacing never drifts. The word gap is at least 0.28 em. Layout runs on the
device (`cart_page`: word wrap, bold headings, hanging bullets, paging).

A "style" is `family * n_sizes + size`: 0-2 = Lexend small/medium/large, 3-5 =
Bitter. `cart_font_families()` and `cart_font_family_name()` list the families.

- Chunk: `u8 n_faces, u8 n_sizes, u8 n_families, u8 pad`, `u32 face_offset[n_faces]`,
  `char family_name[n_families][16]`; face i = `(family * n_sizes + size) * 2 + bold`.
- Face: 12-byte header `u8 px, line_h, ascent, flags, first_char (32), n_glyphs;
  u16 n_kern, space_adv16, pad`; glyphs `{u16 bitmap_off, u8 w, h, i8 x_off, y_off,
  u16 adv16}`; kern pairs `{u8 left, u8 right, i16 value16}` sorted by (left, right),
  only pairs of a quarter pixel or more; then bitmaps (w*h bits, MSB first, each
  starting on a byte). Glyph slot 127 is the bullet. 12.4 fixed point throughout.

## Context tags and voice lists: `NTAG`, `TAGN`, `PHRS` (optional, added 2026-11-06)

Content is filtered by a sticky context: a state, plus optional season and
activity. Tags and contexts are 96 bits: region bits 0-63 (by default the 50 US
states + DC by postal code, alphabetical: 0 = AK ... 47 = WA ... 50 = WY), season
64-67 (spring, summer, fall, winter), activity 68-75 (hiking, water, vehicle, camp).
Untagged content is global / standard and shows in every state. The firmware
keeps the chosen context in NVS until the person picks another; an empty
dimension in the context means show all. Within one dimension a node matches if it
has no tags there, the context has nothing chosen there, or they share a bit.
Nodes flagged emergency always match.

- `NTAG`: 12 bytes per node, three `u32` words (bits 0-31, 32-63, 64-95). A node
  with no tags in a dimension inherits its tree's.
- `TAGN`: `u8 n_dims, 3 pad`; `n_dims` x `{u16 name_text, u8 shift, u8 width}`;
  then `u16 label_text[96]` per bit (`0xFFFF` unused).
- `PHRS`: 6 bytes per ESP-SR command id: `u16 spoken_text, u16 node, u8 flags, u8 pad`.
  Flags: 1 = always in the list, 2 = section entry (points at a tree root).

Bits follow the order of the vocabulary, so a custom list in `contexts.json` is
append-only (the saved context on a device must keep its meaning across updates).

`cart_voice_list(c, tree, &ctx, ids, 200)` builds one MultiNet list (200
commands max): always phrases, then every section's entry phrases, then the
current section's phrases, then any other visible phrase until the list is full.
Identical wording is kept once. Load it with `esp_mn_commands_clear()`,
`esp_mn_commands_add(id, text)` and `esp_mn_commands_update()`; the recognised id
indexes `CMDS`/`PHRS` as before.

### Intermediate additions (all optional)

- `contexts.json`: `{"region": [...], "season": [...], "activity": [...],
  "groups": {"pnw": ["wa", "or", "id"]}}`. Entries are id strings or
  `{"id", "label"}`. Leave `region` out to use the built-in state list.
- `trees[].tags`, `nodes[].tags`: e.g. `{"region": ["wa", "or"], "season": ["winter"]}`.
  Region tags may use group names, `"all"`, and exclusions: `{"region": ["-fl", "-hi"]}`
  means everywhere except Florida and Hawaii.
- `nodes[].emergency: true` marks a single node as never filtered.
- `phrases[].always: true` puts a phrase in every list.
