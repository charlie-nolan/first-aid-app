# Model size comparison: s vs m vs l (round-2 data, 2026-11-21/22)

All three models trained on the same round-2 data and scored with the same code (decoder at 3b0e3a6+,
device float32 front end from 801cde0, tourniquet pronunciations from 273a452). Metrics only; no prompt text.

| size | channels x blocks | weights | int8 model.bin | int8 vs float frames |
|---|---|---|---|---|
| s (`firstaid`) | 192 x 12 | 0.52 M | 0.55 MB | 96.6% |
| m (`firstaid2m`) | 256 x 14 | 1.03 M | 1.07 MB | 96.6% |
| l (`firstaid2l`) | 320 x 16 | 1.79 M | 1.85 MB | 96.7% |

## Charlie's 120 EVAL clips (his voice, device PDM mic)

| | s | m | l |
|---|---|---|---|
| key words caught | 69% | 78% | 78% |
| wrong key words per clip | 0.30 | 0.31 | 0.29 |
| right sentence in top 5 | 27% | 27% | 32% |
| word error rate (not the target metric) | 48% | 45% | 39% |

## Held-out synthetic voices

| word error clean / wind / water | s 39 / 39 / 50% | m 33 / 33 / 41% | l 27 / 27 / 36% |
|---|---|---|---|
| key words caught in wind | | 83% | 85% |

Noise reject: 99-100% for all; none of Charlie's real clips wrongly rejected.

## Life-threat words (m vs l)

Life words: breathing, bleeding, spurting, choking, unresponsive, CPR, pulse, EpiPen, tourniquet, seizure, ...
counted as found anywhere in the 5-best.

| found in 5-best | m | l |
|---|---|---|
| synthetic, clean | 86% | 94% |
| wind | 88% | 94% |
| running water | 83% | 87% |
| breathing noise | 80% | 88% |
| "not/stopped + life word" kept together | 64/77 | 68/77 |
| life-word clips wrongly rejected | 20 / 1,752 | 24 / 1,752 |
| false life words per clip | 0.01 | 0.01 |
| **total life words missed (all sets)** | **276** | **155** |

Charlie's own EVAL has only 13 life words (l 11, m 10): too few to judge; round-3 recordings fix that.

Tourniquet fix (273a452): CMUdict only had TER-ni-kit; added TOR-ni-kit and TOR-ni-ket. It was the most
missed life word (m 288, l 146 misses before). l clean 119 -> 125/144, wind 238 -> 258/288.

## On the board (XIAO ESP32-S3, firmware thread)

- m: 0.49 s per second of audio (front end 0.05, model 0.26, search 0.18), bit-exact with these exports.
- l: estimated ~0.69 s/s (1.77x m's model compute); board timing pending.
- l voice cart is 6.67 MB, 446 KB over the plain XIAO's 6.23 MB partition; fits the Plus. Plan: m on the
  plain bench board, l on the Plus.

## Read

Bigger keeps helping on whole sentences, noise and life words; on Charlie's key words m and l tie, so the
next gain there comes from his own recordings (round 3: word-gap PAIRS from 2a3b0cc, ANSWERS).
