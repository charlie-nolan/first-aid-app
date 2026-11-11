# Design history

*Covers the project from the first written plan (approx. early November 2026, before 2026-11-04) to 2026-11-11.*

It started as a side project split off my cyberdeck work: a hand-crank, offline e-ink box that listens for a few spoken phrases and shows a cited passage from a public-domain manual.
It is now a working breadboard prototype on a XIAO ESP32-S3: a compressed "cartridge" of 19 manuals, cited question trees, a tile UI driven by buttons, and a power and rescue-signal design ready for the bench.
What changed most is the priority order: buttons now come first and voice is an optional extra, there is no AI on the device at all, and the content is aimed at civilian wilderness first aid rather than military casualty care.

I worked the design through with an AI assistant as a sounding board and reference; the direction, constraints and key calls are mine.

> **Not medical advice.** This is a personal prototype, not a certified medical device. Most content trees are still unreviewed. In a real emergency, call for help or send an SOS first, and get proper first-aid training.

## How to read this

- **Short on time?** Read [My key calls](#my-key-calls) and the [Timeline](#timeline). That's the whole story in two screens.
- **Every idea uses the same five headings:** Origin, Versions (oldest to current, with dates), Why it changed, Status, and Options considered and rejected.
- **Status words:** *kept* (in the current design), *changed* (kept, but different from the first version), *dropped* (removed), *parked* (good idea, not now).
- **Numbers marked "estimate" are estimates.** Bench measurements are called out as measured. Dates are 2026; "approx." or "undated" means I can't pin the day down.

## Contents

1. [My key calls](#my-key-calls)
2. [Timeline](#timeline)
3. [Idea-by-idea breakdown](#idea-by-idea-breakdown)
   - [A. Design rules and scope](#a-design-rules-and-scope)
   - [B. Content and sources](#b-content-and-sources)
   - [C. The cartridge format](#c-the-cartridge-format)
   - [D. Content pipeline](#d-content-pipeline)
   - [E. Firmware and UI](#e-firmware-and-ui)
   - [F. Voice](#f-voice)
   - [G. Power](#g-power)
   - [H. Rescue signals](#h-rescue-signals)
   - [I. Hardware and enclosure](#i-hardware-and-enclosure)
   - [J. Testing and CI](#j-testing-and-ci)
4. [Dead ends and lessons](#dead-ends-and-lessons)
5. [Key decisions](#key-decisions)
6. [Open questions](#open-questions)

## My key calls

These are the decisions and catches that were clearly mine.

1. **The device never writes its own advice.** Every screen is a verbatim passage with its citation. A wrong page is recoverable; invented medical advice is not.
2. **No AI on the device, ever.** It has to work fully offline in the backcountry. Desktop-only test helpers are fine because they never ship.
3. **Tiny hardware on purpose.** I wanted to force the software to fit one ESP32-S3 by keeping the needed intelligence narrow, not by bolting on a bigger co-processor.
4. **Buttons first, voice second.** It must work perfectly with buttons alone, one button per answer. Voice is a research project on top.
5. **The "Pokemon ROM" library.** Bake the manuals once into an immutable, compressed cartridge read straight from flash, like a Game Boy ROM, instead of a database.
6. **Swap the laser for a mesh radio.** A LoRa/Meshtastic SOS board took the laser's space, and both the white and IR beads became bare, wide "flood" SOS strobes on two faces.
7. **Catching the missing emergency power store.** I expected a capacitor bank that could run the board with a dead or frozen battery. It wasn't in the plan, so it went in, along with a separate crank charger.
8. **An isolated fire-starter circuit.** A hard RUN/FIRE switch kills the board and routes the crank and supercaps to a thin heating coil. My vape-coil experience corrected the first estimate that a crank could never make a wire glow.
9. **Civilian wilderness focus.** I noticed the voice phrases drawn from military manuals skewed toward violent injuries, and re-aimed the whole product at everyday backcountry first aid.
10. **Keep the screen on its driver board.** Stripping the bare e-ink panel and buying an adapter was fragile and fiddly. Desoldering the tall header and wiring the board directly costs only about 3 mm of thickness.
11. **The start screen.** No "call for help" lock screen, no Bleeding page and no book list on wake. Home is two big tiles, Injury and Survival, that open into a tree of tiles.
12. **Sticky, state-level region.** Pick a US state once (mine is Washington) and it stays until I change it, so content that doesn't apply there drops out of menus and voice.

## Timeline

| Date | Stage | What changed | Why |
|---|---|---|---|
| Undated (approx. late Oct to early Nov) | Idea | Split the first-aid reference off my cyberdeck project; early mock-up with a front D-pad, about 96 × 42 mm | I wanted a pocket device that works with no signal |
| 2026-11-04 | Plan v0 | Written plan: XIAO S3 Sense, ESP-SR fixed phrases where the command ID is the section ID, N20 hand crank, 6 edge buttons, cited fallback trees, "Pokemon ROM" library | First full parts list and build plan, about 72 × 38 × 17 mm and 45-50 g (estimate) |
| 2026-11-04 | Hardware | Crank and USB-C only; no walking harvester, solar off v1 | A pocket shaker gives about 0.1-1 mW (estimate), far below a crank |
| 2026-11-04 | Hardware | Brain moved from the XIAO Sense to the XIAO S3 Plus plus a separate PDM mic | The Sense is listed at about 3 mA in deep sleep, which would drain the battery in days; the Plus has 16 MB flash |
| 2026-11-04 | Content | ATP 4-02.11 (2026) became the primary first-aid source; TC 4-02.1 is backup only | TC 4-02.1 is superseded by the 2026 manual |
| 2026-11-04 | Cartridge | Cartridge v1 built: Re-Pair + Huffman text at 37% of raw size, context-coded pictures, a ~1.8 KB C reader | Benchmarked 10+ methods; this beat zstd and deflate with each passage still decodable on its own |
| 2026-11-04 | Signals | IR strobe and a green line laser added for search and rescue | Night signalling for goggle-equipped crews; a laser to pinpoint position |
| 2026-11-06 | Signals | Laser dropped; Meshtastic LoRa SOS on a second, power-switched board took its place; white and IR became bare flood strobes, both flashing SOS | A laser brings regulatory and eye-safety baggage; mesh is more useful and needs no radio code from me |
| 2026-11-06 | Hardware | Red front light dropped; warm white only (off, dim, bright); screen stays on its HAT board | Simpler; frees a pin; avoids stripping the panel |
| 2026-11-06 | Firmware | First ESP-IDF C firmware with a PC screen simulator; sticky region, season and activity tags; fonts redone CrossInk-style at 10/12/15 px | I wanted to test on a plain S3 while the Plus boards ship; the first text render looked bad |
| 2026-11-07 | Repo + bench | Code moved to its own repo; first real compile and flash; e-ink working on the breadboard | Moving from cloud drafts to hardware on my desk |
| 2026-11-07 | Power | Supercap bank, cold-charge cutoff and a separate crank charger added; fire-starter circuit designed | Run with a dead or frozen battery; make hard cranking worth it; last-resort fire |
| 2026-11-07 | Content | 19 public manuals converted; all shipped as a text-only "Unreviewed" reference shelf, separate from signed-off topics | More coverage now without pretending it's been reviewed |
| 2026-11-07 | UI | "Call for help" wake screen removed; custom sleep art; Bleeding no longer the start page | I didn't like either on the bench |
| 2026-11-08 | Scope | Product re-aimed at civilian wilderness first aid; phrases rewritten in everyday wording | Military sources skewed the voice list toward violent injuries |
| 2026-11-08 | Content | Copyrighted wilderness first-aid books kept out of the build | The repo stays public-domain only |
| 2026-11-08 | Testing | CI on every PR; card text checked against the PDF pages; voice stress test with synthetic phrasings | Catch broken trees and text drift automatically |
| 2026-11-08 | Rules | No generative model on the device; buttons-first; one button per answer, max 4 answers per question | A GPT-2-in-assembly project prompted the question; the answer was no |
| 2026-11-08 | UI | Home became two tiles, Injury and Survival; the book list moved into the menu | I wanted to see and use the real firmware, not a library browser |
| 2026-11-10 | Pipeline | Desktop GPU kit: local model drafts tree structure from the manuals; Plus firmware builds | Heavy work runs on my own PC; drafts still go through every check |
| 2026-11-10 | Release | README rewritten for going public, GPL-3.0 licence added | Preparing the repo to be open source |

## Idea-by-idea breakdown

### A. Design rules and scope

#### A1. Never write its own advice

- **Origin:** The first plan (2026-11-04). The device looks up a section of a vetted manual and shows the real passage with a citation.
- **Versions:**
  - v1 (2026-11-04): every tree node cites manual + section; no node without a source.
  - v2 (2026-11-04): enforced in the builder: verbatim passages only, labels 40 characters or fewer.
  - v3 (2026-11-08): voice may only open "answer cards" built from signed-off trees, never raw manual search.
  - Current (2026-11-08): plus a refused-passage list, a cites lock, and a rule against shipping any passage cut off mid-sentence.
- **Why it changed:** It didn't weaken; each new feature (voice, reference shelf, model-drafted trees) got its own guard.
- **Status:** Kept.
- **Options considered and rejected:** A small model that writes or summarises answers (rejected: it can invent advice).

#### A2. No AI on the device

- **Origin:** The first plan already had "v1 has no model". It became a hard rule on 2026-11-08.
- **Versions:**
  - v1 (2026-11-04): no model in v1; an optional tiny text classifier "for v2"; free-form speech only on a Pi Zero-class "big brother".
  - v2 (2026-11-08): no generative model, ever. Only a possible tiny classifier that outputs card numbers.
  - Current (2026-11-08): desktop-only AI helpers are allowed for testing and drafting, because they never ship.
- **Why it changed:** The device is for the backcountry with no signal, and anything that writes text can make up advice.
- **Status:** Kept.
- **Options considered and rejected:** GPT-2 class models, llama2.c-size models (too weak, still generative), a Linux co-processor for speech-to-text (parked, see F6).

#### A3. Tiny hardware on purpose

- **Origin:** My stated design philosophy (2026-11-08).
- **Versions:**
  - v1 (2026-11-08): keep the intelligence narrow and make it fit one ESP32-S3 with workarounds.
  - Current: applied to voice (small word sets, ask-don't-guess), to the cartridge (look-ups, not computation), and to build-time work (heavy jobs on the desktop, tables baked into flash).
- **Why it changed:** n/a. It sharpened after evaluating bigger add-on boards.
- **Status:** Kept.
- **Options considered and rejected:** A Linux module for free-form speech (too big and costly for the gain), a second S3 just to read extra buttons.

#### A4. Buttons first, voice second

- **Origin:** The plan had voice as the headline and buttons as a menu. I flipped that on 2026-11-08.
- **Versions:**
  - v1 (2026-11-04): "listens for spoken phrases, or you pick from a button menu".
  - Current (2026-11-08): must work perfectly with buttons alone. Each answer sits next to its button; no topic more than 3 presses deep; voice can only jump to screens buttons can reach.
- **Why it changed:** Voice will miss in wind, stress and with strangers' voices. Buttons can't.
- **Status:** Changed.
- **Options considered and rejected:** Scroll-and-select lists for choices (too many presses under stress).

#### A5. Civilian wilderness first aid

- **Origin:** The first plan covered "wilderness first-aid and survival". The early sources were mostly military.
- **Versions:**
  - v1 (2026-11-04): first aid plus survival shelves (bushcraft, plants, fire, traps, weather).
  - v2 (2026-11-07): added violence and CBRN response manuals (casualty care only, no fighting or weapons).
  - Current (2026-11-08): a standard civilian first-aid assistant, aimed at wilderness first aid with no signal. Everyday topics first: cuts, sprains, fractures, hypothermia, heat, burns, bites, CPR, choking.
- **Why it changed:** Phrases drawn from military manuals leaned toward gunshot and stab wounds, which isn't what a hiker needs most.
- **Status:** Changed.
- **Options considered and rejected:** Keeping a military-first topic order.

#### A6. Scope: a personal, open-source prototype

- **Origin:** Clarified on 2026-11-08.
- **Versions:**
  - v1 (2026-11-08): a prototype for my own use and experimentation.
  - Current (2026-11-10): the repo is being prepared to go public under GPL-3.0, with public-domain content only.
- **Why it changed:** n/a. Being clear it's a personal prototype kept the scope honest: public-domain content only, and a clear "not medical advice" notice.
- **Status:** Kept.
- **Options considered and rejected:** None recorded here.

#### A7. "Job cartridges" for edge devices

- **Origin:** Undated (before 2026-11-04). A bigger idea behind this project: a fixed "console" plus a swappable, highly compressed "cartridge" for one job.
- **Versions:**
  - v1 (approx. 2026-11-04): this device is cartridge #1 (ROM + trees + phrase list); other cartridges could cover garden or soil sensing or a mesh trail buddy.
  - Current (2026-11-08): extended to "each cartridge brings its own ears", a small per-cartridge word-set voice model (research idea).
- **Why it changed:** n/a.
- **Status:** Parked.
- **Options considered and rejected:** Running code "inside" model weights (far too slow; the cartridge runs on the real CPU).

### B. Content and sources

#### B1. The public-domain rule

- **Origin:** First plan (2026-11-04): US Army and Air Force manuals are public domain.
- **Versions:**
  - v1 (2026-11-04): only copies whose cover says "approved for public release; distribution is unlimited".
  - Current (2026-11-07): any US Government work counts (17 USC 105), even without a printed distribution line. Each PDF is pinned by sha256; figures marked copyrighted or "used with permission" are refused.
- **Why it changed:** Three federal civilian manuals (a ship's medicine guide, FEMA CERT and a Navy snakes book) had no statement printed. The statement is a military habit, not the legal test.
- **Status:** Changed.
- **Options considered and rejected:** Dropping manuals with no printed statement.

#### B2. Primary first-aid manual

- **Origin:** The plan named TC 4-02.1 *First Aid*.
- **Versions:**
  - v1 (2026-11-04): TC 4-02.1.
  - Current (2026-11-04): ATP 4-02.11 (released 2026) is primary; TC 4-02.1 only fills gaps, still cited as itself.
- **Why it changed:** TC 4-02.1 is superseded by the 2026 manual.
- **Status:** Changed.
- **Options considered and rejected:** ATP 4-02.11 only; staying on TC 4-02.1.

#### B3. Breadth of sources

- **Origin:** I wanted "as many manuals as possible for all scenarios and environments" (2026-11-04).
- **Versions:**
  - v1 (2026-11-04): core four (ATP 4-02.11, TC 4-02.1, ATP 3-50.21 *Survival*, AFH 10-644 *SERE*).
  - v2 (2026-11-04): 18 manuals in four tiers (cold, mountain, jungle, desert, heat/cold/altitude medical bulletins, field hygiene, medicine at sea, venomous snakes, FEMA).
  - v3 (2026-11-07): a fifth tier for violence and CBRN casualty care; 19 manuals converted, about 10,000 sections.
  - Current (2026-11-08): plus civilian US Government web pages (weather service lightning safety, Ready.gov heat, cold and storms).
- **Why it changed:** Each gap I hit (civilian wording, lightning, heat) pulled in another public source.
- **Status:** Changed.
- **Options considered and rejected:** archive.org metadata files instead of PDFs (no text in them); scraping sites that block bots (I saved those by hand in a browser instead).

#### B4. What got excluded, and why

- **Origin:** Built up over the project.
- **Versions:**
  - 2026-11-04: no mushrooms. A 1-bit picture can't make a mushroom ID safe.
  - 2026-11-04: folk remedies only where a source covers them, with that source's warnings.
  - 2026-11-08: copyrighted wilderness first-aid books stay out of the public build.
  - 2026-11-08: an outdated snakebite technique in older manuals is blocked by a refused-passage list, because the 2026 manual contradicts it.
  - 2026-11-08: a civilian source with a printed temperature-conversion error is never quoted at that line.
  - 2026-11-08: one medical bulletin's table came out jumbled and mixes in prescription doses, so it isn't used for on-screen steps.
- **Why it changed:** Each exclusion came from a specific risk found while reading the sources.
- **Status:** Kept.
- **Options considered and rejected:** Mushroom ID keys (an earlier brainstorm listed them; I'd already ruled them out).

#### B5. Copyrighted wilderness first-aid books

- **Origin:** 2026-11-08. Commercial wilderness-medicine books fit a civilian backcountry audience better than military manuals.
- **Versions:**
  - Current (2026-11-08): none of them are in this repo or the public cartridge (all rights reserved). Public-domain manuals only.
- **Why it changed:** n/a.
- **Status:** Kept.
- **Options considered and rejected:** Including copyrighted books in the public build.

#### B6. Edible plants key

- **Origin:** First plan (2026-11-04): a yes/no dichotomous key with zoomed 1-bit crops, Pacific Northwest first, ending at a species page with toxic look-alikes or "no match: don't eat it".
- **Versions:**
  - v1 (2026-11-04): planned shelf; text must state colours because 1-bit can't show them.
  - Current (2026-11-10): no public-domain PNW plant guide found; plant trees left out of the model drafting run.
- **Why it changed:** No suitable source yet.
- **Status:** Parked.
- **Options considered and rejected:** Mushroom keys (dropped).

#### B7. Unreviewed reference shelf

- **Origin:** 2026-11-07, once all the manuals were converted.
- **Versions:**
  - v1 (2026-11-07): offered with pictures, text-only, or reviewed topics only. I chose text-only.
  - v2 (2026-11-07): every manual browsable by chapter and section, every page cited and footed "Unreviewed", 3.1 MB.
  - Current (2026-11-08): the "unreviewed" flag lives inside the cartridge itself; manuals appear in five folders (First aid, Wilderness first aid, Survival, Terrain, Emergencies).
- **Why it changed:** More coverage without pretending it's been signed off. Text-only kept it inside the plain board's 8 MB flash.
- **Status:** Kept.
- **Options considered and rejected:** Reviewed topics only (too little content early on); shelf with pictures (too big for the bench board).

### C. The cartridge format

#### C1. "Pokemon ROM" instead of a database

- **Origin:** My compression idea (undated, before 2026-11-04): the manuals never change, so bake them once like a Game Boy ROM.
- **Versions:**
  - v1 (2026-11-04): dictionary-coded text, a precomputed word index, a fixed-size tree table, 1-bit pictures, a tiny C reader, its own flash partition.
  - v2 (2026-11-04): format "CRT1": tagged chunks read in place with `esp_partition_mmap`, 16-byte node records, voice command ID → node table.
  - Current (2026-11-08): new format with an unreviewed flag and sparse tag tables; full public library 3.66 MB, 14,755 nodes, every node read back with 0 errors.
- **Why it changed:** Features were added; the core idea held.
- **Status:** Kept.
- **Options considered and rejected:** SQLite or compressed SQLite rows (hundreds of KB of code plus RAM cache, no gain for a read-only book); streaming chunks; hand-written assembly for the data (text stays data; only the reader could be hand-tuned).

#### C2. Text compression

- **Origin:** The plan's "common words become 1-2 byte codes, like Pokemon".
- **Versions:**
  - v1 (approx. 2026-11-04): word dictionary with 1-2 byte codes (52.7% of raw in the benchmark).
  - Current (2026-11-04): Re-Pair (up to 4,095 symbols) + canonical Huffman, 37% of raw on 140 KB of stand-in text, each passage decodable alone.
- **Why it changed:** Benchmarked against deflate, zstd with trained dictionaries, brotli, BPE and word Huffman; Re-Pair won with a tiny decoder.
- **Status:** Changed.
- **Options considered and rejected:** zstd (bigger decoder, worse ratio here), arithmetic/ANS coding (within 0.3% of Huffman, not worth it).

#### C3. Picture compression

- **Origin:** Plan (2026-11-04): threshold to 1-bit, then CCITT G4 or RLE.
- **Versions:**
  - v1 (2026-11-04): G4 or RLE.
  - Current (2026-11-04): a 10-pixel context model with a binary range coder, 20-40% smaller than G4 on line drawings; decodes in a few ms (estimate).
- **Why it changed:** Benchmarks on sample drawings.
- **Status:** Changed.
- **Options considered and rejected:** PackBits; G4. A "trained start" for the coder is an untested idea.

#### C4. Fonts inside the cartridge

- **Origin:** I wanted CrossInk-style switchable font sizes (2026-11-04).
- **Versions:**
  - v1 (2026-11-06): three sizes of Lexend Deca with word wrap and paging in the reader.
  - v2 (2026-11-06): after I said it looked bad next to CrossInk: hinted rendering, 1/16-pixel advances, kerning, Bitter added as a second font.
  - Current (2026-11-06): 10 / 12 / 15 px, default smallest, about 5 lines per page.
- **Why it changed:** The first render had drifting letter spacing and broken thin strokes; I also wanted more text per page.
- **Status:** Changed.
- **Options considered and rejected:** 9/10/11 px (no large size for bad light), keeping 12/15/19 px.

#### C5. Context tags and "segmenting"

- **Origin:** My question (2026-11-06): could content be segmented (for example hiking + summer) so the device only touches that part?
- **Versions:**
  - v1 (2026-11-06): segments for speed rejected (the device only touches the page it shows); sticky context kept for filtering and voice.
  - v2 (2026-11-06): tags for all 50 states + DC, season and activity; untagged = global; exclusions like "everywhere except Florida"; emergency pages never filtered.
  - Current (2026-11-07): tags stored only for tagged nodes, saving 145 KB.
- **Why it changed:** The real win was fewer wrong voice matches and less clutter, not speed.
- **Status:** Changed.
- **Options considered and rejected:** Splitting the library into segments for RAM or speed.

#### C6. Cartridge integrity

- **Origin:** Plan (2026-11-04): a checksum makes the ROM tamper-evident.
- **Versions:**
  - v1 (2026-11-04): checksum.
  - Current: signature vs plain CRC still undecided.
- **Why it changed:** n/a.
- **Status:** Parked.
- **Options considered and rejected:** None yet.

### D. Content pipeline

#### D1. The builder (`plb`)

- **Origin:** Plan (2026-11-04): PDFs → text + figures → numbered sections → YAML trees → phrases → pictures → `library.bin`.
- **Versions:**
  - v1 (2026-11-04): written and tested on a placeholder PDF (15 tests), because the cloud box couldn't download the manuals.
  - v2 (2026-11-06): reuses my cyberdeck PDF tools as a figure adapter; passage text stays on direct PDF extraction so OCR can't change a word.
  - v3 (2026-11-07): run on real manuals on my desktop; extractor fixes for headings, bullets, two-column pages, degree signs, tables and CAUTION/WARNING boxes (264 kept and tied to their steps).
  - Current (2026-11-08): web-page ingest and 100 tests.
- **Why it changed:** Real manuals broke assumptions (non-Army numbering, dropped warning boxes, dropped hyphens).
- **Status:** Kept.
- **Options considered and rejected:** A 215-threshold image binarisation from my older script (the builder's own conversion keeps thin lines better).

#### D2. Fallback trees

- **Origin:** Plan (2026-11-04): "what if I don't have X": kit, then improvised, then nothing, always ending at the safest thing still possible.
- **Versions:**
  - v1 (2026-11-04): branching trees in YAML, every node cited.
  - v2 (2026-11-07): first real tree (Bleeding) drafted and reviewed as a PR.
  - Current (2026-11-08): at most 4 answers per question (build fails otherwise); press-count report (Bleeding: 3); emergency treat cards first, "How do I tell?" one tap away.
- **Why it changed:** Buttons-first made the 4-answer cap and shallow trees necessary.
- **Status:** Changed.
- **Options considered and rejected:** Long single-page topics (a 12-page card is unusable in the cold).

#### D3. Verbatim checks

- **Origin:** 2026-11-04: the builder checks every node shows word-for-word cited text.
- **Versions:**
  - v1 (2026-11-04): passages compared through the real C reader.
  - Current (2026-11-08): `plb verbatim` re-reads each cited PDF page with a second PDF reader. Bleeding: 38 of 39 passages exact; one layout question left for me to check on the printed page.
- **Why it changed:** A second reader catches extraction errors the first one can't see.
- **Status:** Kept.
- **Options considered and rejected:** Trusting a single extraction.

#### D4. Sign-off, cites lock and refused list

- **Origin:** 2026-11-04: nothing ships until I sign off a tree.
- **Versions:**
  - v1 (2026-11-07): each tree is a PR with a review page showing every screen.
  - Current (2026-11-08): cited sections are locked so a shifted section ID is caught; a refused list bans specific passages; trees carry an `unreviewed` flag shown on the device until I approve them.
- **Why it changed:** Re-extraction moved section IDs, which could silently change what a card says.
- **Status:** Kept.
- **Options considered and rejected:** None recorded.

#### D5. Drafting trees with a local model

- **Origin:** I wanted the heavy back-end work done on my own PC and GPU (2026-11-08, 2026-11-10).
- **Versions:**
  - v1 (2026-11-08): trees hand-drafted; no GPU needed.
  - v2 (2026-11-10): a local model reads all 21 public sources, finds topic candidates (about 1,200), merges them (180 topics) and drafts trees. It only picks sections and writes button labels; every draft goes through the same checks.
  - Current (2026-11-10): the model must return schema-checked JSON; any citation to a section that doesn't exist is dropped, not guessed. 56 trees passed in the first half of the run.
- **Why it changed:** Free-form YAML from the model broke on stray colons, and a few drafts cited sections that didn't exist.
- **Status:** Kept (desktop only).
- **Options considered and rejected:** Letting a model write card text (breaks A1).

#### D6. Answer cards

- **Origin:** My idea (2026-11-08): voice shouldn't just drop you on a manual page; give a focused screen built from the source.
- **Versions:**
  - v1 (2026-11-08): mock-up: warning box, one verbatim step per screen in big text, TALK shows the full cited passage.
  - Current: mock awaiting my OK; corner-button answer screens are built.
- **Why it changed:** A plain keyword search over raw manuals found the right passage for only 13 of 20 everyday questions.
- **Status:** Changed (in progress).
- **Options considered and rejected:** Voice that searches raw manuals.

### E. Firmware and UI

#### E1. Toolchain and simulator

- **Origin:** 2026-11-06, when I asked whether the software was built and if it was C++.
- **Versions:**
  - v1 (2026-11-06): ESP-IDF 5.3 in C; same reader file as the PC tools; UI code free of ESP calls so a PC simulator renders real screens.
  - v2 (2026-11-07): first real compile and flash on my desktop.
  - Current (2026-11-10): separate plain (8 MB, no voice, 0.33 MB app) and Plus (16 MB, voice, 1.15 MB app + 2.9 MB speech model) builds.
- **Why it changed:** The Plus build needed its own dependency lock for the speech library.
- **Status:** Kept.
- **Options considered and rejected:** Arduino (the speech engine and flash-mapped reads fit ESP-IDF better).

#### E2. Start screen

- **Origin:** Plan (2026-11-04): every wake first shows a "life threat? call for help first" screen.
- **Versions:**
  - v1 (2026-11-04): emergency-call screen on wake.
  - v2 (2026-11-07): I disliked the call screen and the Bleeding tree as default; wake opened the Library folders instead.
  - v3 (2026-11-08): I said the Library shouldn't be the start page either; it moved into the menu; wake resumes where I was reading.
  - Current (2026-11-08): two tiles, Injury and Survival, opening into folder tiles and topic tiles; signed-off topics first, drafts next, manuals last.
- **Why it changed:** Using the bench board showed what felt wrong.
- **Status:** Changed.
- **Options considered and rejected:** Bleeding as default, Library as home, emergency-call lock screen.

#### E3. Sleep screen

- **Origin:** Plan (2026-11-04): a user-chosen image, CrossPoint-style; default was a bleeding-control card.
- **Versions:**
  - v1 (2026-11-04): bleeding card.
  - v2 (2026-11-07): mountains with a sasquatch as a test image, redrawn as a classic striding silhouette.
  - Current (2026-11-07): a silly sasquatch picture over an alpine valley, "Press any button to wake" (bench only; third-party art, not shipped).
- **Why it changed:** I'm an e-ink enjoyer and this is my first DIY e-ink build, so I wanted something fun on the screen, not just unreviewed pages and military manuals. The emergency line moved off the sleep image.
- **Status:** Changed.
- **Options considered and rejected:** Keeping the emergency line on the sleep screen.

#### E4. Virtual windows and resume

- **Origin:** Plan (2026-11-04): up to 5 "desktops" for side tangents, kept in RTC memory through deep sleep.
- **Versions:**
  - v1 (2026-11-04): tap left/right to switch, hold to open or close.
  - Current (2026-11-08): kept; wake returns to the last page.
- **Why it changed:** n/a.
- **Status:** Kept.
- **Options considered and rejected:** None recorded.

#### E5. E-ink refresh

- **Origin:** First bench run (2026-11-07): tearing, static and corruption that got worse over time.
- **Versions:**
  - v1 (2026-11-07): fixed partial-refresh bookkeeping; added a fixed-wait fallback when the BUSY line is silent.
  - v2 (2026-11-07): I found the real cause: my BUSY jumper was one row off. Added a two-button forced full refresh and a full refresh every 5 turns.
  - Current (2026-11-07): CrossInk-style single-flash clean-up. Measured: 0.6 s per page turn, 2.3 s full refresh, 55 refreshes with no fault.
- **Why it changed:** Bench evidence.
- **Status:** Changed.
- **Options considered and rejected:** Inverted display to fight ghosting (not needed once refresh was fixed). Grey edge shading still to test.

#### E6. Context picker ("Where and when...")

- **Origin:** 2026-11-06, from C5.
- **Versions:**
  - Current (2026-11-06): menu item to set state, season and activity (each can be "Any"); saved in flash so it survives sleep, power loss and updates.
- **Why it changed:** n/a.
- **Status:** Kept.
- **Options considered and rejected:** None recorded.

#### E7. Battery indicator and reserve

- **Origin:** Plan (2026-11-04): ~20% reserve that only emergency pages can use.
- **Versions:**
  - v1 (2026-11-06): low-battery lock leaves only emergency pages open.
  - v2 (2026-11-07): battery % icon top right, via a two-resistor divider on the bench board (measured 4.10 V, ~91% while charging).
  - Current (2026-11-10): a board with no battery attached is treated as "unknown", not "empty".
- **Why it changed:** On USB with no cell, 0 V read as a dead battery and locked everything.
- **Status:** Kept.
- **Options considered and rejected:** None recorded.

#### E8. Continuous reading

- **Origin:** My observation (2026-11-07) that CrossInk on a tiny chip chunks content invisibly.
- **Versions:**
  - Current (2026-11-07): reading rolls from section to section and chapter to chapter; each manual remembers its place.
- **Why it changed:** n/a.
- **Status:** Kept.
- **Options considered and rejected:** None recorded.

### F. Voice

#### F1. Fixed phrases, command ID = section ID

- **Origin:** Plan (2026-11-04): ESP-SR returns a command ID; that ID can be the section ID, so v1 needs no model.
- **Versions:**
  - v1 (2026-11-04): about 100-200 fixed phrases.
  - Current (2026-11-06): MultiNet lists of up to 200 phrases, swapped at runtime per section and context.
- **Why it changed:** n/a.
- **Status:** Kept.
- **Options considered and rejected:** Free-form speech-to-text on the device (the S3 can't).

#### F2. Listening mode

- **Origin:** Plan (2026-11-04): wake word plus push-to-talk.
- **Versions:**
  - v1 (2026-11-04): WakeNet wake word + MultiNet.
  - Current (2026-11-06): push-to-talk on TALK only; mic powered from a GPIO so it draws nothing when idle.
- **Why it changed:** Saves power; no always-listening.
- **Status:** Changed.
- **Options considered and rejected:** Always-on wake word.

#### F3. Phrase lists

- **Origin:** I wanted the voice list as full as possible, with sections kept separate (2026-11-06).
- **Versions:**
  - v1 (2026-11-06): each list = emergency phrases + one jump phrase per section + as many phrasings of the current section as fit.
  - v2 (2026-11-07): Bleeding phrases written from 67 realistic utterances.
  - Current (2026-11-08): all 96 phrases loaded on the home screen; misses at home dropped from 158 to 105 of 172 test asks (stand-in scorer).
- **Why it changed:** The stress test showed most specific asks failed right after wake.
- **Status:** Changed.
- **Options considered and rejected:** Only entry phrases at home.

#### F4. Civilian wording

- **Origin:** My catch (2026-11-08) that phrases were biased toward violence.
- **Versions:**
  - v1 (2026-11-07): phrases mined from military manuals.
  - Current (2026-11-08): everyday backcountry wording (axe, knife, trail falls, dog bite); military jargon removed.
- **Why it changed:** See A5.
- **Status:** Changed.
- **Options considered and rejected:** Keeping both sets.

#### F5. Ask instead of guess

- **Origin:** Stress test (2026-11-08): sound-alike pairs (have / don't have a tool, knee / neck, arm / armpit).
- **Versions:**
  - Current (2026-11-08): forks become yes/no or picker screens on the buttons; top-3 with confirm when unsure; "Did you mean A or B?" for known sound-alike pairs (needs a small format addition).
- **Why it changed:** Fits A3: make the voice hear one small word set at a time.
- **Status:** Kept (partly built).
- **Options considered and rejected:** Trusting the single top guess.

#### F6. On-device models and add-on boards

- **Origin:** 2026-11-08, when I found a GPT-2 written in assembly and asked if it could be the librarian.
- **Versions:**
  - v1 (2026-11-08): GPT-2 in assembly needs ~1 GB RAM and Apple Silicon; it can't run on an S3.
  - v2 (2026-11-08): possible v2 tiny intent or slot classifier (< 300 KB) that outputs card IDs only.
  - Current (2026-11-08): no generative model; classifier only if bench tests show more than about 1 in 10 real asks missing. A second PDM mic sharing the same wires for noise filtering is suggested.
- **Why it changed:** Size, power and the "never invent" rule.
- **Status:** Parked.
- **Options considered and rejected:** A small Linux board with an NPU for free-form speech (big, about $50, slow to boot); Pi Zero 2 (bigger, thirstier); standalone voice modules (just another fixed list); assembly rewrites of the speech models (already hand-tuned by the vendor).

#### F7. Testing voice offline

- **Origin:** I asked (2026-11-08) for tests that voice commands actually open relevant content.
- **Versions:**
  - v1 (2026-11-08): `plb voice` with 264 synthetic civilian phrasings and a phonetic stand-in scorer; 0 of 92 off-topic asks opened a Bleeding card.
  - Current (2026-11-10): offline text-to-speech clips in many voices with wind, water and breath noise, played into the real board to score actual ESP-SR (scripts ready, not yet run on a Plus).
- **Why it changed:** The stand-in scorer is only a gap-finder.
- **Status:** Kept.
- **Options considered and rejected:** None recorded.

### G. Power

#### G1. Energy source

- **Origin:** Plan (2026-11-04): hand crank by default, solar optional.
- **Versions:**
  - v1 (2026-11-04): crank + optional solar panel with an MPPT module.
  - v2 (2026-11-04): I asked about a tiny walking/shake harvester; dropped.
  - Current (2026-11-04): crank and USB-C only.
- **Why it changed:** A pocket shaker gives about 0.1-1 mW and adds 10-15 g (estimate); solar wasn't worth it for v1.
- **Status:** Changed.
- **Options considered and rejected:** Shake harvester, solar in v1.

#### G2. Crank mechanics

- **Origin:** Plan (2026-11-04).
- **Versions:**
  - v1 (2026-11-04): N20 6 V 100 rpm gear motor as a generator (150 rpm fallback), lying flat; a 1:1 bevel pair turns the output 90° to a folding arm on the back.
  - v2 (undated, shared 2026-11-06): a 693ZZ bearing on the crank shaft protects the motor's bushing from side loads.
  - Current (2026-11-06): straight-tooth module 0.5, 20T, 3 mm bore brass pair; hub length to measure.
- **Why it changed:** Parts arrived and were checked against the layout.
- **Status:** Kept.
- **Options considered and rejected:** Worm-gear motors (they can't be back-driven); swapping in a 50 rpm gearbox (a $3 comfort test only; it can't beat the charger limit).

#### G3. Crank electronics

- **Origin:** Plan (2026-11-04): bridge, 470 µF cap, 5 V buck-boost into the XIAO.
- **Versions:**
  - v1 (2026-11-04): 1N5817 bridge, 470 µF 10 V cap, generic buck-boost.
  - v2 (2026-11-04): 25 V cap plus a 12 V zener clamp; Pololu S9V11F5.
  - Current (2026-11-09): SS14 Schottkys for the bridge (same rating, surface-mount).
- **Why it changed:** Fast cranking can push the motor past what the regulator and a 10 V cap can take.
- **Status:** Changed.
- **Options considered and rejected:** No clamp.

#### G4. Charging path

- **Origin:** 2026-11-07, when I asked how long a full charge by crank would take.
- **Versions:**
  - v1 (2026-11-04): crank → regulator → the XIAO's built-in charger (~100 mA). About 1 mAh per minute, roughly 5 h to full (estimate).
  - Current (2026-11-07): my idea of a separate charging circuit that doesn't touch the ESP: a CN3063 charges the cell at ~250 mA from the crank; the XIAO charger handles USB only. Roughly 8-12 questions per minute of hard cranking, ~2 h full (estimates).
- **Why it changed:** The XIAO charger, not the motor, was the bottleneck.
- **Status:** Changed (optional upgrade; the device works without it).
- **Options considered and rejected:** Higher gear ratios alone.

#### G5. Supercap bank

- **Origin:** My catch (2026-11-07): I remembered capacitors acting as a mini emergency battery; they weren't in the plan.
- **Versions:**
  - v1 (2026-11-04): one 470 µF cap, spike smoothing only.
  - Current (2026-11-07): two 2.7 V 3 F (or 3.3 F) supercaps in series with balancing resistors, feeding the 5 V pin through a diode. About 8 J, about 8 page turns after 10-20 s of cranking (estimate). May grow the case about 4 mm.
- **Why it changed:** The device must run with a dead or frozen battery.
- **Status:** Kept (to bench test).
- **Options considered and rejected:** Coin-cell supercaps (ESR too high).

#### G6. Cold-charge cutoff

- **Origin:** Plan (2026-11-04) noted a LiPo must not charge below 0 °C.
- **Versions:**
  - v1 (2026-11-04): "needs a temperature cutoff, or keep it in an inner pocket".
  - Current (2026-11-07): an MCP9509H temperature switch gates a P-MOSFET in the battery line at about +2 °C, no GPIO needed; the CN3063 adds its own thermistor cutoff.
- **Why it changed:** Made concrete when the supercaps arrived in the design.
- **Status:** Kept.
- **Options considered and rejected:** Relying on body warmth.

#### G7. Battery and budget

- **Origin:** Plan (2026-11-04): ~300 mAh 602030 LiPo, 1,000+ lookups (estimate).
- **Versions:**
  - v1 (2026-11-04): 300 mAh; bigger 2.9"/1000 mAh build considered.
  - Current (2026-11-06): 300 mAh kept; it only gives about 0.6 A in bursts, so only one LED lights at a time.
- **Why it changed:** The LED current limit came from the battery, not the LEDs.
- **Status:** Kept.
- **Options considered and rejected:** The 95 × 45 × 22 mm, 2.9" + 1000 mAh variant.

#### G8. Fire starter

- **Origin:** My idea (2026-11-07): a "break glass" emergency mode for fire.
- **Versions:**
  - v1 (2026-11-07): puncture the battery, or route the crank to a kanthal micro-coil.
  - v2 (2026-11-07): first estimate said a crank can't make a coil glow; I pushed back from vape-coil experience, and the corrected numbers show a very thin, short wire needs only about 0.25-0.5 W (estimate).
  - v3 (2026-11-07): supercaps dump a short high-current burst into a low-ohm coil.
  - Current (2026-11-07): a sealed 3-pole RUN/FIRE toggle kills the board and makes an isolated crank + supercap loop to the coil; a hold-to-glow button on a MOSFET; recessed plug-in lead; battery kept out of the loop. Ferro rod slot as backup. Break-glass page only from a cited source.
- **Why it changed:** Safety (board isolated, no LiPo in the loop) and my correction of the power math.
- **Status:** Kept (needs the motor resistance and glow bench test).
- **Options considered and rejected:** Puncturing the LiPo (toxic smoke, uncontrollable fire); LiPo in the coil loop (its protection trips at about 3 A); vape-style 15-40 W coils from the crank.

### H. Rescue signals

#### H1. Radio SOS

- **Origin:** My question (2026-11-04) about a tiny last-resort radio beacon.
- **Versions:**
  - v1 (2026-11-04): a home-built distress beacon is illegal and untrustworthy; free Bluetooth/Wi-Fi SOS from the ESP32 instead; LoRa as a v2 idea.
  - v2 (2026-11-06): ESP-NOW long-range beacon from an earlier brainstorm judged a buddy feature, not an SOS (only other ESPs hear it).
  - Current (2026-11-06): a second plain XIAO S3 + LoRa board running stock Meshtastic, power-switched by one pin on SOS; its detection-sensor feature broadcasts the alert (to verify on the bench). No GPS yet.
- **Why it changed:** Mesh coverage keeps growing, and stock firmware means I write no radio code.
- **Status:** Changed.
- **Options considered and rejected:** DIY 406 MHz beacon; ESP-NOW as SOS; writing Meshtastic packets myself.

#### H2. Laser

- **Origin:** My idea (2026-11-04): a powerful green laser to signal position, crank-powered.
- **Versions:**
  - v1 (2026-11-04): 5 mW line laser, menu mode with warnings, two-button hold, 10 s cutoff.
  - v2 (2026-11-04): I rejected red (poor range) and accepted a bigger case for green.
  - v3 (2026-11-06): 12 × 35 mm green module, case to about 91 × 38 × 18 mm.
  - Current (2026-11-06): dropped, replaced by the mesh radio.
- **Why it changed:** Regulatory and eye-safety burden; for passive all-night signalling the IR strobe is more useful.
- **Status:** Dropped.
- **Options considered and rejected:** High-power narrow beam (can blind the crew coming to help); red laser.

#### H3. White and IR strobes

- **Origin:** My idea (2026-11-04): IR flood + normal light for night search crews.
- **Versions:**
  - v1 (approx. 2026-11-04): 1 W 3535 white and 850 nm IR.
  - v2 (2026-11-06): XP-G3 on an 8 mm star, then 5 mm IR to save width.
  - v3 (2026-11-06): with the laser gone, bare wide-angle beads, no lens. I argued IR should flood a "bubble" around me rather than aim.
  - Current (2026-11-06): 3 W natural-white and 3 W 850 nm bare 8 mm beads, one pair on the end face and one on the back; both flash Morse SOS; ~0.45 A pulses; one LED at a time. White bead also the dimmable flashlight, on its own pin.
- **Why it changed:** Fit, brightness per mA, and the flood argument for use under trees.
- **Status:** Changed.
- **Options considered and rejected:** Desoldering 3535 chips off star boards; cutting a 20 mm star down; 940 nm IR (less visible to goggles); plain IR strobe without SOS (SOS reads as distress and costs nothing).

#### H4. Screen front light

- **Origin:** Plan (2026-11-04): red and warm-white micro LEDs at both edges of the cover; long-press BACK cycles off, red, white.
- **Versions:**
  - v1 (2026-11-04): red + white.
  - Current (2026-11-06): warm white only; long-press BACK steps off, dim, bright.
- **Why it changed:** Simpler, fewer parts, one more free pin.
- **Status:** Changed.
- **Options considered and rejected:** Red for night vision.

#### H5. CPR cadence LED

- **Origin:** An earlier brainstorm (undated) suggested step-by-step triage including CPR cadence.
- **Versions:**
  - Current (2026-11-06): an LED blinking at the cadence, since e-ink is too slow to blink.
- **Why it changed:** n/a.
- **Status:** Kept (in the design, not yet built).
- **Options considered and rejected:** Blinking the e-ink.

### I. Hardware and enclosure

#### I1. Main board

- **Origin:** Plan (2026-11-04): XIAO ESP32-S3 Sense (mic, 8 MB PSRAM, built in).
- **Versions:**
  - v1 (2026-11-04): Sense.
  - Current (2026-11-04): XIAO S3 Plus (16 MB flash, 20 GPIO) + separate PDM mic. Plain XIAO S3 boards used on the bench meanwhile.
- **Why it changed:** Sleep current and flash space.
- **Status:** Changed.
- **Options considered and rejected:** Keeping the Sense; a generic S3 board with a separate charger (more wiring, bigger box); a second S3 for extra buttons on the bench.

#### I2. Microphone

- **Origin:** 2026-11-04 with the Plus swap.
- **Versions:**
  - Current (2026-11-06): Adafruit PDM breakout, SEL to GND, 2 pins.
- **Why it changed:** n/a.
- **Status:** Kept.
- **Options considered and rejected:** I2S mics (3 pins, and one has a known timing quirk on the ESP32); analog electret mics.

#### I3. Screen and cover

- **Origin:** Plan (2026-11-04): 2.13" 250 × 122 B/W e-ink behind 1.5-2 mm polycarbonate.
- **Versions:**
  - v1 (2026-11-04): bare panel for the case.
  - v2 (2026-11-06): strip the panel off one HAT and add an FPC adapter.
  - Current (2026-11-06): keep the Waveshare HAT, desolder its 40-pin header, wire to its holes; case ~3 mm thicker.
- **Why it changed:** Stripping risks cracking the glass or tearing the ribbon, and needs an extra board.
- **Status:** Changed.
- **Options considered and rejected:** Acrylic cover (cracks); the 2.9" screen (bigger case).

#### I4. Buttons and pins

- **Origin:** Plan (2026-11-04): 6 sealed IP67 edge buttons, no trackball, no face D-pad.
- **Versions:**
  - v0 (undated): front D-pad mock-up, 96 × 42 mm.
  - v1 (2026-11-04): five buttons on one analog pin through a resistor ladder.
  - Current (2026-11-04 to 2026-11-07): each button on its own RTC GPIO (all can wake the board); every Plus pin is now used.
- **Why it changed:** The Plus has enough pins, and own pins allow wake and simultaneous presses.
- **Status:** Changed.
- **Options considered and rejected:** Trackball; face D-pad; resistor ladder.

#### I5. Case

- **Origin:** Plan (2026-11-04): smallest and lightest possible, PETG, olive green and red.
- **Versions:**
  - v1 (2026-11-04): about 72 × 38 × 15-17 mm, 45-50 g (estimate).
  - v2 (2026-11-04): 81 × 38 × 17 mm, ~57 g with signals; 88 × 38 × 17 mm, ~60 g with the green laser.
  - v3 (2026-11-06): ~91 × 38 × 18 mm with the laser module; ~91 × 38 × 21 mm with the HAT.
  - Current (2026-11-07): ~91 × 38 × 21 mm, ~63 g (estimate); may grow ~4 mm for supercaps and 5-8 mm for the fire toggle. Flat 0.8 mm silicone gasket, M2 heat-set inserts, sealed buttons, vent over the mic, O-ring on the crank shaft, conformal coat.
- **Why it changed:** Every added feature cost millimetres; I accepted that for function.
- **Status:** Changed.
- **Options considered and rejected:** PLA (softens in a hot car).

#### I6. Assembly method

- **Origin:** My question (2026-11-06) on how to assemble such small parts.
- **Versions:**
  - Current (2026-11-06): test sub-assemblies first; perfboard for v1, a small custom PCB later once the circuit is proven.
- **Why it changed:** n/a.
- **Status:** Kept.
- **Options considered and rejected:** Going straight to a PCB.

#### I7. Switching the LEDs

- **Origin:** 2026-11-06: why the beads need MOSFETs (an S3 pin gives tens of mA; a bead wants ~0.5 A).
- **Versions:**
  - Current (2026-11-07): TO-220 logic-level MOSFETs on the breadboard; SOT-23 AO3400 (N) and AO3401 (P) in the case.
- **Why it changed:** n/a.
- **Status:** Kept.
- **Options considered and rejected:** CMOS logic gate chips (outputs far too weak); high-voltage MOSFETs that aren't logic-level.

### J. Testing and CI

#### J1. Bench bring-up

- **Origin:** Plan (2026-11-04): one evening: screen, deep sleep, wake on a button, sleep current under 50 µA.
- **Versions:**
  - v1 (2026-11-07): plain S3 flashed, e-ink wired; BUSY wiring fault found and fixed; battery and divider soldered.
  - v2 (2026-11-08): a test build where press, hold and double-tap stand in for the 3 missing buttons, so I can use the real 6-button UI on 3 buttons.
  - Current (2026-11-10): newest build running with 9 topics; LED stations, crank and supercap tests next.
- **Why it changed:** The plain S3 has no spare pins.
- **Status:** Kept.
- **Options considered and rejected:** A resistor-ladder button hack; trading the BUSY wire and battery pin for buttons.

#### J2. Continuous integration

- **Origin:** 2026-11-08, from a review of which software practices the project could use.
- **Versions:**
  - Current (2026-11-08): every PR runs the content schema check, pipeline tests, cartridge reader tests and a bench-cartridge rebuild check, with test fixtures only (no PDFs needed).
- **Why it changed:** It caught a stale test cartridge after a format change on the same day.
- **Status:** Kept.
- **Options considered and rejected:** None recorded.

#### J3. Desktop-only AI test helpers

- **Origin:** 2026-11-08, with the verbatim and voice tests.
- **Versions:**
  - v1 (2026-11-08): optional AI judge for unclear text mismatches and an AI phrase generator.
  - v2 (2026-11-08): removed, when I thought they would run on the device.
  - Current (2026-11-08): restored as opt-in desktop flags once I saw they never ship; advisory only, never clears a card.
- **Why it changed:** A misunderstanding, fixed by checking where the code runs.
- **Status:** Kept (desktop only).
- **Options considered and rejected:** None recorded.

## Dead ends and lessons

| Idea | Why it died | Lesson |
|---|---|---|
| Walking/shake harvester | ~0.1-1 mW at pocket size; adds 10-15 g (estimate) | Do the power math before the mechanism |
| Solar in v1 | Not worth the size for a crank-first device | Cut optional power sources until there's a measured need |
| XIAO S3 Sense | ~3 mA listed deep-sleep current would drain the cell in days | Check sleep current on the datasheet first; it decides battery life |
| Resistor-ladder buttons | Own GPIOs allow wake and simultaneous presses | Spend pins on what has to wake the board |
| Stripping the e-ink panel + adapter | Fragile glass and ribbon, extra board, small gain | 3 mm of thickness is cheaper than a cracked screen |
| Green laser | Regulatory and eye-safety baggage; IR strobe more useful at night | Compare signals by the real rescue scenario, not by range |
| Red front light | Little gain, extra parts and a pin | Remove modes nobody needs |
| ESP-NOW "beacon" | Only other ESP devices can hear it | A distress signal must reach equipment searchers actually carry |
| DIY radio distress beacon | Illegal and untrustworthy | Carry certified gear alongside; build only legal layers |
| Puncturing the battery for fire | Toxic smoke, uncontrollable fire, device destroyed | Isolate the dangerous path with a hard switch instead |
| "Crank can't glow a coil" | My vape-coil experience showed the estimate assumed the wrong coil | Challenge estimates with hands-on experience; match load to source |
| SQLite / segmenting for speed | The device only decodes the page on screen | Measure where the cost really is before optimising |
| Generative model on device | ~100× too big; can invent advice | Narrow the job until a lookup is enough |
| Linux co-processor for speech | Big, ~$50, slow boot, power-hungry | Revisit only if bench data shows the cheap fixes fail |
| Voice over raw manuals | Keyword search found the right passage only 13/20 times | Point voice at curated cards, not the whole library |
| Violence-biased phrases | Mined from military manuals | Source bias leaks into everything downstream |
| Library or Bleeding as home | Felt wrong in daily use | Use the real device before settling UI |
| "Call for help" wake screen | Got in the way every wake | Put safety reminders where they help, not on every screen |
| Free-form YAML from a model | One stray colon broke drafts; some cited missing sections | Force a schema, and drop anything you can't verify |
| Mushroom ID | 1-bit pictures can't make it safe | Leave out content you can't make safe |

## Key decisions

- **D1.** The device shows only verbatim, cited passages; it never writes advice.
- **D2.** No AI or generative model on the device; desktop-only helpers are allowed.
- **D3.** Buttons first: one button per answer, max 4 answers per question, no topic deeper than about 3 presses.
- **D4.** Voice is optional: ESP-SR fixed phrases, push-to-talk, command ID maps to a card.
- **D5.** Voice opens only answer cards from signed-off trees.
- **D6.** Primary target: civilian wilderness first aid; everyday topics first.
- **D7.** Public build uses US Government works only (17 USC 105), each PDF pinned by sha256.
- **D8.** ATP 4-02.11 (2026) is the primary first-aid source; TC 4-02.1 backup only.
- **D9.** Copyrighted books stay out of the repo and the public cartridge; public-domain manuals only.
- **D10.** No mushroom ID; outdated passages refused by list; cut-off passages never shipped.
- **D11.** All converted manuals ship as a text-only "Unreviewed" reference shelf, separate from signed-off topics.
- **D12.** Content lives in an immutable flash cartridge (`library.bin`), no database or filesystem.
- **D13.** Text: Re-Pair + canonical Huffman, per-passage decodable.
- **D14.** Pictures: 1-bit, context model + range coder.
- **D15.** Fonts: Lexend Deca or Bitter at 10/12/15 px, default 10, CrossInk-style rendering.
- **D16.** Sticky context: US state (or global), season, activity; emergency pages never hidden.
- **D17.** Firmware in ESP-IDF C with a PC simulator; plain and Plus builds.
- **D18.** Home screen: Injury and Survival tiles; Library in the menu; wake resumes reading.
- **D19.** Sleep screen: custom image with "Press any button to wake"; no emergency lock screen.
- **D20.** E-ink: partial refresh with periodic single-flash clean-up; two-button forced refresh.
- **D21.** Brain: XIAO ESP32-S3 Plus + PDM mic.
- **D22.** Screen: Waveshare 2.13" HAT kept in the case, header removed.
- **D23.** Six sealed edge buttons, each on its own RTC GPIO.
- **D24.** Power: crank and USB-C only; 300 mAh LiPo.
- **D25.** Crank: N20 6 V 100 rpm, 1:1 straight bevel pair, bearing-supported shaft, 25 V cap + 12 V zener, Pololu S9V11F5.
- **D26.** Supercap bank lets the crank run the board with a dead or frozen battery.
- **D27.** Cold-charge cutoff at about +2 °C.
- **D28.** Separate CN3063 crank charger (optional upgrade).
- **D29.** Fire starter: hard RUN/FIRE switch, isolated crank + supercap loop, hold-to-glow button, no LiPo in the loop.
- **D30.** No laser.
- **D31.** Meshtastic SOS on a second, power-switched board running stock firmware.
- **D32.** White and IR bare bead strobes on two faces, both Morse SOS, one LED at a time.
- **D33.** Front light warm white only: off, dim, bright.
- **D34.** Heavy build work (tree drafting, voice clips, baking) runs on my desktop GPU, never in the device.
- **D35.** Model-drafted trees use schema-checked output; invented citations are dropped; I sign off every tree.
- **D36.** CI on every PR with fixtures only.
- **D37.** Code licensed GPL-3.0; fonts under OFL.

## Open questions

- **Content review:** sign off the drafted trees (CPR and choking); confirm the topic order (fastest killers first was used as the default).
- **Page checks:** one Bleeding passage layout to check against printed pages.
- **Bench tests still to run:** LED strobe stations, half crank test (motor, bridge, cap, zener), supercap run time, motor resistance and coil glow, sleep current.
- **Measurements:** HAT mounting holes, bevel gear hub length, bead fit, and where the supercaps and fire toggle go in the layout.
- **CN3063:** confirm the current-set resistor formula.
- **Mesh:** verify the Meshtastic detection-sensor SOS on the bench; decide on GPS.
- **Voice:** real ESP-SR miss rate on the Plus with recorded clips and real voices; whether the sound-alike picker and a second mic are needed.
- **Answer cards:** approve the card mock-up and build it into the firmware.
- **Display:** test grey edge shading on the real panel.
- **Switches:** are the owned switch kits actually IP67, or do I need sealed ones?
- **Desktop tools:** the opt-in AI judge restore lives on a side branch, not main yet.
- **Cartridge integrity:** CRC or signature.
- **Plants:** find a public-domain PNW plant source, or leave the shelf out.
- **Name:** the product still needs one.
