# Intermediate format (builder -> packer)

`plb build` writes `build/intermediate/`. The packer (ROM compression thread) turns it into `library.bin`. Everything is UTF-8 JSON, but text values are **printable ASCII plus `\n`** only.

| File | Contents |
|---|---|
| `manifest.json` | `build_id`, `build_time`, `counts`, `errors`, `sources: [{id, title, edition, sha256, distribution}]` |
| `passages.jsonl` | one per line: `{id, source, para, heading, citation, text}`. `id` is `MANUAL/PARA`, or `MANUAL/PARA#start-end` for a verbatim excerpt. `text` is verbatim manual text. A `\n` starts a new line (sub-paragraph "a.", "(1)"). A line starting `- ` is a bullet, and one starting `## ` is a heading. |
| `trees.json` | `{trees: [{id, title, shelf, root, emergency}], nodes: [{id, key, tree, label, passages, lookalikes, figure, choices: [{label, to, kind}], species, no_match}], home_tree, sleep_picture}`. Node `id`s are dense 0..N-1, and each tree's root comes first. `kind` is one of kit, improvised, nothing or next. `figure` is a picture id. |
| `phrases.json` | `[{cmd_id, phrase, spoken, node}]`. `cmd_id` is dense 0..M-1 and is the ESP-SR command id. `phrase` is normalized to a-z and spaces. |
| `phrases.txt` / `phrases.h` | the same list for ESP-SR (`id<TAB>phrase`, and a C array) |
| `pictures.json` + `pictures/*.png` | `[{id, figure, file, w, h, pan, caption, source, citation}]`. 1-bit PNGs, already trimmed and scaled: at most 250x122, or up to 750x366 when `pan` is true. Black = ink. |
| `strings.json` | fixed UI strings (WAKE_911, DISCLAIMER, NO_MATCH, PLANT_WARNING) |
| `contexts.json` | always written: `{season, activity, groups}` from `content/contexts.yaml` (region left out = built-in 50 states + DC) |

Context filtering (romkit FORMAT.md, 2026-11-06): `trees[].tags` and `nodes[].tags` are `{region|season|activity: [ids]}`. Region ids are postal codes, group names, `all`, or `-xx` exclusions. Untagged means global. `nodes[].emergency: true` makes a node never filtered. `phrases[].always: true` puts a phrase in every voice list. There is no global phrase cap. Instead, for every tree, always + all entry phrases + that tree's own phrases must fit in 200.

Labels (tree titles, node and choice labels) are at most 40 chars. They are the only hand-written text apart from `strings.json`.
