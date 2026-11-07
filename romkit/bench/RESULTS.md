# Benchmarks (2026-11-04)

Stand-in text: the MIT-licensed Trail Sense survival guide (8 chapters, 140,564
bytes after cleanup, split at headings into 296 passages averaging 474 bytes),
because the Army manual sites are blocked from the build machine. Re-run on the
real manuals once they're in. Every method is scored with each passage decodable
on its own, plus the shared tables it needs.

## Text (bench.py)

| method | body | tables | total | % of raw | decoder |
|---|---|---|---|---|---|
| raw | 140,564 | 0 | 140,564 | 100% | - |
| deflate per passage | 80,026 | 0 | 80,026 | 56.9% | in ESP32 ROM (tinfl) |
| deflate + 16 KB dictionary | 50,615 | 16,384 | 66,999 | 47.7% | in ESP32 ROM |
| zstd + 8 KB trained dict | 52,983 | 8,192 | 61,175 | 43.5% | ~25-40 KB code (estimate) |
| zstd + 32 KB trained dict | 39,173 | 32,768 | 71,941 | 51.2% | same |
| brotli q11 per passage | 66,606 | 0 | 66,606 | 47.4% | carries a 120 KB dictionary |
| character Huffman | 80,886 | 85 | 80,971 | 57.6% | tiny |
| Pokemon-style word dictionary (1-2 byte codes) | 58,462 | 15,682 | 74,144 | 52.7% | tiny |
| byte-pair encoding, 1 byte per token | 73,761 | 428 | 74,189 | 52.8% | tiny |
| word Huffman ("huffword") | 51,559 | 16,229 | 67,788 | 48.2% | small |
| Re-Pair 2048 symbols + Huffman | 46,701 | 7,213 | 53,914 | 38.4% | tiny |
| **Re-Pair 4095 symbols + Huffman (chosen)** | 39,761 | 13,964 | 53,725 | 38.2% | ~1.8 KB |

The shipped packer stores the chosen codec's tables in 12.3 KB (symbol id =
Huffman index, 3-byte entries), so the real ROM is 37% of raw text here.

Scaling (same codec, growing prefixes of the corpus): 45 KB -> 44.9%, 67 KB ->
42.3%, 140 KB -> 37.1% total. The tables are a fixed cost, so a 1-2 MB library
should land near the body figure, about 28-30% (estimate).

Entropy check: canonical Huffman is within 0.3% of the entropy of the symbol
stream, so ANS/arithmetic coding would gain almost nothing for text.

## Pictures (picbench.py), bytes per 250x122 picture

| picture | raw | PackBits | CCITT G4 | context model (chosen) |
|---|---|---|---|---|
| line diagram (synthetic) | 3,904 | 1,040 | 550 | 434 |
| outline drawing | 3,904 | 572 | 404 | 245 |
| scanned text | 3,904 | 1,828 | 1,598 | 1,191 |
| photo crop, dithered | 3,904 | 3,610 | 7,110 | 1,952 |
| photo, thresholded | 3,904 | 2,598 | 794 | 742 |
| 750x366 pan picture | 34,404 | 7,386 | 4,252 | 2,579 |

Test images are scikit-image samples and a drawn diagram, not manual figures.

## Reader size and speed

`cart.c` at -Os: 1,782 bytes on Cortex-M4 Thumb, 2,776 bytes on RV32IMC,
2,996 bytes on x86-64 (Xtensa compiler not available here; expect 2-3 KB).
Host (2.1 GHz Xeon): 13.5 ns per decoded text byte, 96 us per 250x122
context-coded picture. On the 240 MHz ESP32-S3 reading through the flash cache,
expect roughly 20-40x slower (estimate): about 0.2-0.3 ms per 500-byte passage
and 2-4 ms per picture, far below one e-ink refresh.
