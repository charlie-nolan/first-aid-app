# Voice stress test and card text check

Both run on the desktop at build time, fully offline, with no AI model or network
service anywhere. Nothing here goes on the device.

## `plb verbatim`: is every card word for word what the book prints?

`plb test` proves each passage is a substring of its extracted section. `plb verbatim`
checks the extraction itself: it re-reads each cited page straight from the pinned PDF
with a second text engine (pdfium; extraction uses pdfplumber), drops running headers,
footers and page numbers, and compares.

- `exact`: same words and punctuation (list markers and line breaks aside)
- `layout`: same once line-end hyphens, ligatures, quotes, dashes and degree signs are folded.
  Hyphen differences are still listed as notes, because a card can read `lifethreatening`.
- `MISMATCH`: words differ. The word diff is printed. A word the page has and the card
  lacks is always a mismatch, since a dropped "not" looks exactly like that.

```
plb verbatim                 # signed-off topics (fails on any MISMATCH)
plb verbatim --all           # also the unreviewed shelf (slow, about 2 min)
plb --personal verbatim      # personal books too; the report stays in build/
```

A person reads every MISMATCH; nothing clears a card automatically.

## `plb voice`: many ways to say it, against the real phrase list

`content/phrases/synthetic/*.yaml` holds held-out utterances (read and signed off by
Charlie): per node, how a scared hiker or parent might ask, plus `off_topic` asks for
topics with no tree yet that must not open a card. Don't copy them into the trees
wholesale, or the test stops measuring anything.

ESP-SR can't run here, so a stand-in picks the registered phrase that sounds most like
the utterance (rough phonetic key per word, then word and letter similarity) and accepts
it above a threshold. It is a proxy for finding gaps and look-alikes. Bench numbers win.

It scores two listening contexts, as the firmware loads them: `home` (always + topic
entry phrases) and `in-topic` (plus the topic's node phrases). Outcomes: hit, near
(topic's first screen or a step on the way), miss (device says it didn't catch that),
OFF step (right topic, card for another situation), WRONG topic, FALSE (off-topic ask
opened a card). It also lists registered phrases that sound alike across nodes.

```
plb voice                         # report -> build/voice-report.md; fails on OFF/WRONG/FALSE
plb voice --threshold 0.65
```

Snapshots of the first run are in `docs/reports/`.
