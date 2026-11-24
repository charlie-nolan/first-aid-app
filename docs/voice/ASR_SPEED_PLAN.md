# Making our speech recognizer fast on the S3

2026-11-20. Ideas from Oido (lokutor-ai/oido, GPL-3: **read for ideas only, no code copied**) and Espressif's
esp-dsp / esp-nn (Apache 2.0: may be used, keep the license notice), applied to OUR engine
(`firmware/components/asr`, branch claude/project-thread-lvj6uv) and OUR model (`fasr` size m: stem conv
40->256 k5 s2, 14 blocks of depthwise k15 + pointwise 256x256 + residual, head 256->41, causal).

## Where the time goes today (board, 240 MHz, export 24bc72a, after the 2x exact speed-up 09f3987)

| Stage | per second of audio | Why it is slow |
|---|---|---|
| Front end (FFT, mel, log) | ~0.95 s | Done in **double**. The S3 FPU is single precision only, so every double op is emulated in software (tens of cycles each). |
| Model, size m | ~1.9 s (small: ~1.0 s) | Scalar C, one multiply per instruction, channel-major layout. The S3 vector unit does **16 int8 multiply-adds per instruction** and is not used. |
| Beam search | ~0.4 s | Double log/exp (`lse` = exp + log1p) in the inner loop, also emulated. |

## How Oido got faster than real time (what they did, in their words and code)

1. Vector int8 dot products (`ee.vmulas.s8.accx`), with the next weights loaded while the current ones multiply (no load stall).
2. Weights read in place; the chip's D-cache preload engine fetches the next layer's weights in the background (38-41 % faster, same results).
3. Work buffers in internal SRAM (3.8 GB/s) not PSRAM (89 MB/s) or flash (32 MB/s).
4. Float32 front end, real 512-point FFT done as one 256-point complex FFT + a fix-up step.
5. Table-driven exp/log/tanh, written so PC and chip give identical numbers.
6. **Streaming**: the model runs on chunks while you are still talking, so when you stop only the last chunk is left. Text 0.26 s after speech ends.
7. Blank-frame shortcut in the CTC decoder.
8. One core only (their two-core split gave wrong transcripts on real chips).

## What that means for us (estimates, to be measured)

Our model m is ~1.03 M multiply-adds per 20 ms frame = ~52 M per second of audio. Today that runs at ~9 cycles per
multiply-add. The vector unit peaks at 16 per cycle; at a realistic 20-30 % of peak that is **~40-150 ms per second
of audio**, 10-40x faster. Front end in float32 with esp-dsp's S3 FFT: **~10-30 ms per second**. Decoder in float or
integer log domain: **guess ~50-100 ms**. Total roughly **0.1-0.3 s per second of audio**, and with streaming the
words are ready about **0.1-0.3 s after you let go of TALK**. These are estimates; the board decides.

Weight traffic is not a problem for us: we decode a whole utterance at once, so each weight is read once per
utterance (1.07 MB from flash ~ 33 ms), not once per frame. With streaming chunks of ~0.5 s it is ~2-3x per second.

## Experiments (one change each, measured on the board)

Each run: the bench app on the 8 golden clips + the 120 EVAL clips (PC side), recording ms per stage per second of
audio, key words caught, wrong key words per clip, life-threat misses, and whether PC == chip still holds.
Screenshot per run (`<date>_asr-speed_runNN_testNN.png`, metrics only).

| # | Change | Results stay bit-exact? |
|---|---|---|
| E1 | Front end to float32: esp-dsp `dsps_fft2r_fc32` (S3 asm) as a 256-point complex FFT + real fix-up, float mel sum, `logf` | **No, ~1 ulp.** Fix: build the C front end as a PC library and have fasr use it for goldens and eval, so PC == chip again by construction. Needs Charlie's OK to re-baseline. |
| E2 | Pointwise 256x256 convs as a vector GEMM: activations time-major (T x C), rows 16-byte aligned, `ee.vmulas.s8.accx` with two-register load pipelining (our own kernel, pattern as in esp-nn `esp_nn_dot_s8_aligned_esp32s3`) | **Yes.** Integer sums are exact in any order; keep our own power-of-two requantize. ~95 % of the model's work. |
| E3 | Depthwise k15 convs: 16 channels per vector lane accumulators (`ee.vmulas.s8.qacc`, 20-bit lanes; 15 x 127 x 127 = 242k fits) | **Yes.** |
| E4 | Stem conv (40 x 5 = 200 inputs, pad to 208) as im2col + the E2 kernel (too big for 20-bit lanes) | **Yes.** |
| E5 | Work planes in internal SRAM (`fast()`), weights 16-byte aligned in the cart (asrpack), D-cache preload of the next layer | **Yes.** |
| E6 | Softmax and beam search: float32 or fixed-point log domain, `lse` from a table, blank-frame shortcut | **Changes ties.** Judge on the key-word score, not parity. Same PC-library trick if we want exact again. |
| E7 | **Streaming** while TALK is held: front end + model on ~0.32-0.64 s chunks, keep each block's last 14 frames (~50 KB for m). Model is already fully causal (left padding only). | **Yes** for the model (same sums); the decoder can stay at end of speech. |
| E8 | Once fast: try size **l** (320 channels, 16 blocks, ~1.8 M weights) and frame stacking. Speed buys a bigger, more accurate model. | Speech thread retrains; new parity. |

Notes from reading the code (verify): `components/asr/CMakeLists.txt` does not define `FE_FAST`, so the double
front end is what runs. `fq_logp` is also double exp/log per frame and token. `ws_model` uses internal RAM only if
`fast()` succeeds; check that 5 s of audio doesn't fall back to PSRAM. **Exact blank shortcut for E6:** in a frame
where `tok_prune` leaves only blank (no other token, including each beam's last token), every beam becomes
pb' = lse(pb, pnb) + row[blank], pnb' = NEG. The order is unchanged and no merges happen, so add/hash/sort/compact
can be skipped and the result stays bit-identical.

| E9 | PSRAM/flash at 120 MHz (`sdkconfig.psram120`, Charlie OK'd 11-17 as a later step) vs the 80 MHz default. Soak test plus cold test before keeping it; Espressif marks it experimental, with a temperature warning. Matters less once E5 puts hot data in SRAM. | **Yes** (clock only). |

**Order: run E5 (SRAM placement + prefetch) before or together with E2-E4.** Evidence: bitbank2's AnimatedGIF
on the S3 with data in PSRAM took 1691 ms without SIMD and 1742 ms with it, because it was memory-bound. Vector
kernels only pay off once the data is in SRAM. (The ESP32-S31, with 2x PSRAM on its own bus, is a v2-board note only.)

Keep: one core, deterministic math, MultiNet and ESP-SR AFE out (closed). ESP-DL runtime not needed: esp-nn's
kernel patterns inside our engine keep the parity check exact.

## The router (Charlie asked: bigger, smarter, faster?)

The router is ~88 KB int8 and runs in under 1 ms already. Faster math does not make it smarter: on 11-16 router
accuracy was flat past ~100 KB, and **more and better training sentences** were what moved it. The vector kernels
would let it grow 10-50x and still take a few ms, so a bigger router is cheap to try, but only after it gets more
data (Charlie's real phrasings, the phrase-review page, more trees). The form after it stays rules, on purpose.
The biggest accuracy win from the speed work is E8: a bigger speech model, because missed key words are today's
main failure.
