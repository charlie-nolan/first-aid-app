"""Benchmark random-access text packing for the first-aid ROM.

Every passage must decode on its own (the device jumps straight to one node),
so every method is scored per passage, plus the shared tables it needs.
"""
import glob, re, heapq, zlib, collections, sys, struct, json, os
import zstandard, brotli

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------- corpus: split into passages at headings ----------
def load():
    passages = []
    for f in sorted(glob.glob(os.path.join(HERE, 'corpus', 'ts_*.txt'))):
        txt = open(f, encoding='utf-8').read()
        txt = re.sub(r'<!--.*?-->\n?', '', txt)          # keyword comments -> index, not text
        txt = txt.replace('**', '')                       # bold markers: device uses a style byte
        txt = txt.translate(str.maketrans({'–': '-', '—': '-', '≈': '~', '×': 'x', '½': '1/2', '¾': '3/4'}))
        for part in re.split(r'\n(?=#{2,3} )', txt):
            part = part.strip()
            if len(part) > 20:
                passages.append(part.encode('latin-1'))
    return passages

P = load()
RAW = sum(map(len, P))

# ---------- canonical Huffman (length-limited) ----------
def huff_lengths(freq, maxlen=15):
    items = [(f, i) for i, f in freq.items() if f > 0]
    if len(items) == 1:
        return {items[0][1]: 1}
    heap = [(f, n, [s]) for n, (f, s) in enumerate(items)]
    heapq.heapify(heap)
    L = collections.Counter()
    n = len(heap)
    while len(heap) > 1:
        f1, _, a = heapq.heappop(heap); f2, _, b = heapq.heappop(heap)
        for s in a + b: L[s] += 1
        n += 1; heapq.heappush(heap, (f1 + f2, n, a + b))
    # crude length limit: clamp and repair Kraft sum
    L = {s: min(l, maxlen) for s, l in L.items()}
    while sum(2.0 ** -l for l in L.values()) > 1:
        s = max((s for s in L if L[s] < maxlen), key=lambda s: (L[s], -freq[s]))
        L[s] += 1
    return L

def huff_bits(seqs, extra_syms=()):
    freq = collections.Counter()
    for s in seqs: freq.update(s)
    for s in extra_syms: freq[s] += 1
    L = huff_lengths(freq)
    per = [(sum(L[t] for t in s) + 7) // 8 for s in seqs]   # byte-aligned passages
    return per, L

# ---------- methods ----------
results = []
def report(name, per_passage, table_bytes, note=''):
    body = sum(per_passage)
    results.append(dict(name=name, body=body, table=table_bytes, total=body + table_bytes,
                        ratio=(body + table_bytes) / RAW, body_ratio=body / RAW, note=note))

report('raw', [len(p) for p in P], 0)

# 1. zlib per passage, no dictionary vs. with preset dictionary (ESP32 ROM has tinfl)
report('deflate per passage', [len(zlib.compress(p, 9)) - 6 for p in P], 0, 'raw deflate, decoder in ESP32 ROM')
dict_src = zstandard.train_dictionary(16384, P)
zd = dict_src.as_bytes()
def deflate_dict(p):
    c = zlib.compressobj(9, zlib.DEFLATED, -15, zdict=zd[-32768:])
    return len(c.compress(p) + c.flush())
report('deflate + 16K dict', [deflate_dict(p) for p in P], len(zd), 'tinfl in ROM, dict primed into window')

# 2. zstd with trained dictionary
for ds in (8192, 16384, 32768):
    d = zstandard.train_dictionary(ds, P)
    cc = zstandard.ZstdCompressor(level=19, dict_data=d, write_checksum=False,
                                  write_content_size=False, write_dict_id=False)
    report(f'zstd + {ds//1024}K dict', [len(cc.compress(p)) - 4 for p in P], len(d.as_bytes()),
           'needs ~25-40 KB decoder code')

# 3. brotli per passage (uses its built-in 120 KB English dictionary)
report('brotli q11 per passage', [len(brotli.compress(p, quality=11)) for p in P], 0,
       '120 KB built-in dict hides in the decoder')

# 4. order-0 Huffman on characters (Game Boy-ish baseline: char table only)
per, L = huff_bits([list(p) for p in P])
report('char Huffman', per, len(L) + 1, 'shared code-length table only')

# 5. Pokemon/smaz style word dictionary, byte-aligned
def tokenize_words(p):
    return re.findall(rb' ?[A-Za-z]+|.', p, re.S)

def pokemon(P, n1=112, n2=16 * 256):
    cnt = collections.Counter()
    for p in P: cnt.update(t for t in tokenize_words(p) if len(t) > 1)
    # benefit of a 1-byte code = freq*(len-1) - len ; 2-byte = freq*(len-2) - len
    ranked = sorted(cnt, key=lambda w: -(cnt[w] * (len(w) - 1)))
    one = ranked[:n1]
    rest = sorted(ranked[n1:], key=lambda w: -(cnt[w] * (len(w) - 2) - len(w)))
    two = [w for w in rest if cnt[w] * (len(w) - 2) - len(w) - 1 > 0][:n2]
    code = {w: 1 for w in one}; code.update({w: 2 for w in two})
    per = []
    for p in P:
        n = 0
        for t in tokenize_words(p):
            n += code.get(t, len(t))
        per.append(n)
    table = sum(len(w) + 1 for w in one + two)
    return per, table, one, two

per, table, one, two = pokemon(P)
report('Pokemon word dict (1-2 byte codes)', per, table, f'{len(one)} 1-byte + {len(two)} 2-byte words')

# 6. Byte-pair encoding (Re-Pair style) with byte-sized symbols, Gage's classic
def bpe(seqs, nsym_max, base):
    seqs = [list(s) for s in seqs]
    rules = {}
    nxt = base
    while nxt < nsym_max:
        pc = collections.Counter()
        for s in seqs:
            pc.update(zip(s, s[1:]))
        if not pc: break
        (a, b), f = pc.most_common(1)[0]
        if f < 3: break
        rules[nxt] = (a, b)
        for i, s in enumerate(seqs):
            out = []; j = 0
            while j < len(s):
                if j + 1 < len(s) and s[j] == a and s[j + 1] == b:
                    out.append(nxt); j += 2
                else:
                    out.append(s[j]); j += 1
            seqs[i] = out
        nxt += 1
    return seqs, rules

# remap bytes to dense alphabet so free codes are maximal
alpha = sorted(set(b for p in P for b in p))
amap = {b: i for i, b in enumerate(alpha)}
dense = [[amap[b] for b in p] for p in P]
seq8, rules8 = bpe(dense, 256, len(alpha))
report('BPE 8-bit (1 byte/token)', [len(s) for s in seq8], len(alpha) + 2 * len(rules8),
       f'{len(rules8)} pair rules, decoder ~30 lines')

CACHE = os.path.join(HERE, 'bpe_cache.json')
if os.path.exists(CACHE):
    seq_big, rules_big = json.load(open(CACHE))
    rules_big = {int(k): tuple(v) for k, v in rules_big.items()}
else:
    seq_big, rules_big = bpe(dense, 4096, len(alpha))
    json.dump([seq_big, rules_big], open(CACHE, 'w'))

def bpe_prefix(seqs, rules, n):
    """Undo merges above symbol n (rules are ordered), giving a smaller vocab."""
    lim = len(alpha) + n
    def expand(t):
        if t < lim: return [t]
        a, b = rules[t]; return expand(a) + expand(b)
    return [[x for t in s for x in expand(t)] for s in seqs]

for n in (256, 1024, 2048, 4096 - len(alpha)):
    seqs = bpe_prefix(seq_big, rules_big, n) if n < 4096 - len(alpha) else seq_big
    nr = min(n, len(rules_big))
    per, L = huff_bits(seqs)
    # rule table: 2 x 12-bit = 3 bytes per rule; Huffman length table ~ 4 bits/sym
    table = len(alpha) + 3 * nr + (len(L) + 1) // 2
    report(f'BPE {nr} rules + Huffman', per, table, '12-bit symbols, canonical Huffman')
    if n == 2048:
        per12 = [(len(s) * 12 + 7) // 8 for s in seqs]
        report(f'BPE {nr} rules, fixed 12-bit', per12, len(alpha) + 3 * nr, 'no entropy coder at all')

# 7. Word-based Huffman ("huffword", Managing Gigabytes): words + separators
def wordtok(p):
    return re.findall(rb'[A-Za-z0-9]+|[^A-Za-z0-9]+', p)
vocab = collections.Counter()
for p in P: vocab.update(wordtok(p))
spell = {}  # rare words are spelled with char codes to keep the lexicon small
toks = []
for p in P:
    s = []
    for t in wordtok(p):
        if vocab[t] >= 2: s.append(t)
        else: s.append(b'\x00'); s.extend(bytes([c]) for c in t); s.append(b'\x01')
    toks.append(s)
per, L = huff_bits(toks)
lex = sum(len(t) + 1 for t in L if len(t) > 1 or t not in (b'\x00', b'\x01'))
report('word Huffman (huffword)', per, lex + (len(L) + 1) // 2, f'{len(L)} lexicon entries')

# ---------- print ----------
print(f'{len(P)} passages, {RAW} bytes raw, avg {RAW // len(P)} B/passage\n')
print(f'{"method":38s} {"body":>8s} {"tables":>7s} {"total":>8s} {"total%":>7s} {"body%":>6s}  note')
for r in results:
    print(f'{r["name"]:38s} {r["body"]:8d} {r["table"]:7d} {r["total"]:8d} {100*r["ratio"]:6.1f}% {100*r["body_ratio"]:5.1f}%  {r["note"]}')
json.dump(results, open(os.path.join(HERE, 'bench_results.json'), 'w'), indent=1)
