# Keyword search mode

Charlie, 11-20: a second voice mode beside the fill-in-the-blanks intake. Press a different
button, say **one word**, and every related card or topic tile shows in a **4 x 2 grid**, paged,
to browse and pick. No context analysis, no blanks, no life-rule jumps: the person chooses.

## Engine (Python reference: `pipeline/voice_router/vr/search.py`)

`search(word, trees)` returns ranked nodes; `frame(word, trees)` is the screen data.

Words on each node (keyed like the intake: choke/choking/choked match). A word's weight on a node
is the sum of the best weight from each source:

| weight | source | goes to |
|---|---|---|
| 6 | tree title and condition names (`topic_phrases`) | the tree's root tile |
| 3 | the node's label | the node |
| 2 | the node's own phrases | the node |
| 2 | slot phrases whose answer leads to the node (`questions`, or inline `slot:`/`value:` tags), or whose life rule goes to it | the node (cross-tree targets resolve to their own tree) |
| 2 | `search_words: {node: [phrases]}` in the voice slot file (search only: never packed into the ROM or ESP-SR, ignored by intake) | the node |
| 1 | tree phrases | root |
| 1 | labels of choices pointing at the node | the node |
| 1 | the word names the tree's topic | every node of that tree (browse the topic) |

Ranking, in order:
1. **Life cards first**: any node a `life_threat` rule goes to, when its score is at least 2 and at
   least a third of the query's best score, or when the words hold a whole title or condition name of its own tree
   ("nosebleed" puts the won't-stop card first). A weak side match never buries the topic.
2. Nodes of a tree whose whole title or condition name is in the query, before the rest
   ("bleeding": the bleeding tree's severe card before the nosebleed won't-stop card).
3. Score: the sum over the query words. A node must match **every** query word.
4. Root tiles before inner nodes, then depth from the root, then tree name, then node id.

Not indexed: phrases of shared blanks (CPR's own blanks, and any blank 3+ trees carry) outside
their home tree, and the shared awake/breathing life phrases ("won't open her eyes"). Query alias:
"blood" also searches "bleed".

Each node is listed once, under its own tree. At most 48 hits (6 pages). Filler words and words
that match no node give no hits.

## Screen frame (parity)

```json
{"v": 1, "kind": "search", "query": "bleeding", "hits": 13,
 "pages": [[{"title": "Bleeding > Severe bleeding", "node": "wfa-bleeding/severe", "life": true}, ...], ...]}
```

- 8 tiles per page, row-major (top row 1-4, bottom row 5-8).
- `title` is `<tree title> > <node label>`, cut to 78 characters.
- A tile shows the node label. The header strip shows the query, the page (`1/2`) and the hit count.
- Life tiles are drawn inverted (white on black) so they stand out on B/W e-ink.
- Zero hits: "No cards for '<word>'. Hold TALK and say another word."
- Recognizer reject: the usual "Didn't catch that" screen, staying in search mode.

Parity file: `python -m vr.search --trees ... --slots ... --words pipeline/voice_router/search_words.txt --parity OUT.jsonl`.
Titles hold tree labels, so keep personal outputs on the PC.

## Buttons (proposed, doesn't clash with TALK or TALK+WIN_R)

| Where | Button | Does |
|---|---|---|
| anywhere | hold **TALK + WIN_L** | enter search mode and listen for one word (mirror of TALK+WIN_R tabs) |
| grid | hold **TALK** | say another word (stays in search mode) |
| grid | **UP / DOWN** | move the highlight through the 8 tiles (row-major, wraps onto the next/previous page) |
| grid | **WIN_L / WIN_R** | previous / next page |
| grid | short **TALK** | open the highlighted tile |
| card opened from the grid | **BACK** | back to the grid, same page and highlight |
| grid | **BACK** | leave search mode, back to where you were |

The command words keep their meaning in this mode: "back", "again" and "home" are commands, never
queries (so saying "back" for a back injury needs "spine" or "neck").

The recognizer in this mode decodes against the whole lexicon (no per-question subset) and keeps
only the top hypothesis's content words.
