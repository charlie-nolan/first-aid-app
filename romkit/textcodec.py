"""Text codec for cartridge ROMs: Re-Pair (byte-pair grammar) + canonical Huffman.

Every text decodes on its own (random access by index), but all texts share one
grammar and one Huffman table, so common words and phrases ("the casualty",
" apply pressure") cost a few bits anywhere in the library.
"""
import collections, heapq

MAX_SYMS = 4096          # 12-bit symbol space: alphabet + rules
MAX_CODE_LEN = 15


def repair(seqs, n_alpha, max_syms=MAX_SYMS, min_count=3):
    """Re-Pair with incremental pair counts. seqs: lists of ints < n_alpha.
    Returns (encoded seqs, rules) where rules[i] = (left, right) for symbol n_alpha+i."""
    sym, nxt, prv = [], [], []
    for s in seqs:
        base = len(sym)
        for k, c in enumerate(s):
            sym.append(c)
            prv.append(base + k - 1 if k else -1)
            nxt.append(base + k + 1 if k + 1 < len(s) else -1)
    starts = []
    pos = 0
    for s in seqs:
        starts.append(pos if s else -1); pos += len(s)

    occ = collections.defaultdict(set)
    for i in range(len(sym)):
        j = nxt[i]
        if j >= 0:
            occ[(sym[i], sym[j])].add(i)
    heap = [(-len(v), k) for k, v in occ.items()]
    heapq.heapify(heap)

    touched = set()          # pairs that gained occurrences in this merge; pushed once at the end

    def add(i):
        j = nxt[i]
        if i >= 0 and j >= 0:
            p = (sym[i], sym[j]); occ[p].add(i); touched.add(p)

    def rem(i):
        j = nxt[i]
        if i >= 0 and j >= 0:
            occ[(sym[i], sym[j])].discard(i)

    rules = []
    new = n_alpha
    while new < max_syms and heap:
        negc, p = heapq.heappop(heap)
        cur = len(occ.get(p, ()))
        if cur != -negc:
            if cur >= min_count: heapq.heappush(heap, (-cur, p))
            continue
        if cur < min_count:
            break
        a, b = p
        for i in sorted(occ.pop(p)):
            j = nxt[i]
            if sym[i] != a or j < 0 or sym[j] != b:
                continue                       # consumed by an overlapping merge ("aaa")
            pi, nj = prv[i], nxt[j]
            if pi >= 0: rem(pi)
            if nj >= 0: rem(j)
            sym[i] = new; sym[j] = -1
            nxt[i] = nj
            if nj >= 0: prv[nj] = i
            if pi >= 0: add(pi)
            add(i)
        occ.pop(p, None)
        for q in touched:
            n = len(occ.get(q, ()))
            if n >= min_count: heapq.heappush(heap, (-n, q))
        touched.clear()
        rules.append(p)
        new += 1

    out = []
    for st in starts:
        s = []
        i = st
        while i >= 0:
            s.append(sym[i]); i = nxt[i]
        out.append(s)
    return out, rules


def huffman_lengths(freq, maxlen=MAX_CODE_LEN):
    """Package-merge would be optimal; a heap build plus a Kraft repair is close enough."""
    syms = [s for s, f in freq.items() if f > 0]
    if len(syms) == 1:
        return {syms[0]: 1}
    heap = [(freq[s], s, (s,)) for s in syms]
    heapq.heapify(heap)
    depth = collections.Counter()
    tie = max(syms) + 1
    while len(heap) > 1:
        f1, _, a = heapq.heappop(heap); f2, _, b = heapq.heappop(heap)
        for s in a + b: depth[s] += 1
        tie += 1
        heapq.heappush(heap, (f1 + f2, tie, a + b))
    L = {s: min(depth[s], maxlen) for s in syms}
    while sum(2.0 ** -l for l in L.values()) > 1.0:
        s = min((s for s in L if L[s] < maxlen), key=lambda s: (freq[s], -L[s]))
        L[s] += 1
    return L


def canonical(L):
    """Returns (count per length [0..15], symbols sorted by (len, sym), code dict)."""
    count = [0] * (MAX_CODE_LEN + 1)
    for l in L.values(): count[l] += 1
    order = sorted(L, key=lambda s: (L[s], s))
    codes, code, prev = {}, 0, 0
    for s in order:
        code <<= (L[s] - prev); prev = L[s]
        codes[s] = (code, L[s]); code += 1
    return count, order, codes


class BitWriter:
    def __init__(self): self.buf = bytearray(); self.acc = 0; self.n = 0; self.bits = 0
    def put(self, code, length):          # MSB-first
        for k in range(length - 1, -1, -1):
            self.acc = (self.acc << 1) | ((code >> k) & 1); self.n += 1; self.bits += 1
            if self.n == 8: self.buf.append(self.acc); self.acc = 0; self.n = 0
    def getvalue(self):
        b = bytes(self.buf)
        return b + bytes([self.acc << (8 - self.n)]) if self.n else b


TERMINAL = 0xFFF


def build(texts, max_syms=MAX_SYMS - 1):
    """texts: list of bytes (Latin-1). Returns the symbol table, Huffman counts, bit stream, offsets.

    Symbols are renumbered so that symbol i is the i-th canonical Huffman code:
    the decoder's Huffman index IS the symbol id, so no separate sorted-symbol
    list is stored. Each symbol is one 3-byte entry: two 12-bit fields
    (left, right) for a pair rule, or (0xFFF, char) for a terminal."""
    alphabet = sorted(set(b for t in texts for b in t))
    amap = {b: i for i, b in enumerate(alphabet)}
    na = len(alphabet)
    seqs, rules = repair([[amap[b] for b in t] for t in texts], na, max_syms)
    freq = collections.Counter()
    for s in seqs: freq.update(s)
    L = huffman_lengths(freq)
    count, order, codes = canonical(L)
    nsym = na + len(rules)
    rest = [s for s in range(nsym) if s not in L]
    new = {old: i for i, old in enumerate(order + rest)}
    entries = []
    for old in order + rest:
        if old < na: entries.append((TERMINAL, alphabet[old]))
        else: a, b = rules[old - na]; entries.append((new[a], new[b]))
    # expansion depth bound for the decoder's fixed stack
    depth = [0] * nsym
    for i in range(nsym):
        depth[i] = 1 if i < na else 1 + max(depth[rules[i - na][0]], depth[rules[i - na][1]])
    bw = BitWriter()
    offsets = []
    for s in seqs:
        offsets.append(bw.bits)
        for t in s: bw.put(*codes[t])
    offsets.append(bw.bits)
    table = b''.join(bytes([a & 0xFF, (a >> 8) | ((b & 0xF) << 4), b >> 4]) for a, b in entries)
    return dict(entries=entries, table=table, count=count, stream=bw.getvalue(), offsets=offsets,
                n_alpha=na, n_rules=len(rules), max_depth=max(depth))


def decode(model, idx):
    """Reference decoder (mirrors cart.c) for tests."""
    st, buf = model['stream'], []
    bit, end = model['offsets'][idx], model['offsets'][idx + 1]
    E, count = model['entries'], model['count']
    while bit < end:
        code = first = index = 0
        for l in range(1, MAX_CODE_LEN + 1):
            code |= (st[bit >> 3] >> (7 - (bit & 7))) & 1; bit += 1
            c = count[l]
            if code - first < c: sym = index + code - first; break
            index += c; first = (first + c) << 1; code <<= 1
        stack = [sym]
        while stack:
            a, b = E[stack.pop()]
            if a == TERMINAL: buf.append(b)
            else: stack.append(b); stack.append(a)
    return bytes(buf)
