#!/usr/bin/env python3
"""Pack a pipeline intermediate folder into a cartridge ROM (library.bin).

usage: pack.py INTERMEDIATE_DIR OUT.bin   (also writes OUT.ids.json)

Layout (little-endian, every chunk 4-byte aligned so the ESP32 can read it
in place from memory-mapped flash; see FORMAT.md):

  header   'CRT1' u16 ver u16 n_chunks u32 total_size u32 crc32
           u16 home_tree u16 sleep_picture u16 n_nodes u16 n_texts
           then n_chunks x {char tag[4], u32 offset, u32 size}
  chunks   SYMS HUFF TIDX TEXT PASG NODE NPAS CHOI TREE CMDS SRCS PICX PICD STRS FONT NTAG TAGN PHRS
"""
import json, os, re, struct, sys, zlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import textcodec, piccodec, fontpack, contexts

NONE = 0xFFFF
CHOICE_KIND = {'next': 0, 'kit': 1, 'improvised': 2, 'nothing': 3, 'yes': 4, 'no': 5}
NODE_FLAG_EMERGENCY = 1      # readable from the battery reserve
NODE_FLAG_SPECIES = 2
NODE_FLAG_NO_MATCH = 4       # plant key dead end: show the NO_MATCH string


def device_text(s):
    """Pipeline markers -> device control bytes: \x01 heading, \x02 bullet."""
    out = []
    for line in s.split('\n'):
        if line.startswith('## '): line = '\x01' + line[3:]
        elif line.startswith('- '): line = '\x02' + line[2:]
        out.append(line)
    return '\n'.join(out).encode('latin-1')


class Strings:
    def __init__(self): self.ids = {}; self.items = []
    def add(self, s, raw=False):
        if s is None: return NONE
        b = s.encode('latin-1') if raw else device_text(s)
        if b not in self.ids:
            self.ids[b] = len(self.items); self.items.append(b)
        return self.ids[b]


def load(d):
    J = lambda f: json.load(open(os.path.join(d, f)))
    manifest = J('manifest.json')
    passages = [json.loads(l) for l in open(os.path.join(d, 'passages.jsonl')) if l.strip()]
    trees = J('trees.json')
    phrases = J('phrases.json') if os.path.exists(os.path.join(d, 'phrases.json')) else []
    pics = J('pictures.json') if os.path.exists(os.path.join(d, 'pictures.json')) else []
    strings = J('strings.json') if os.path.exists(os.path.join(d, 'strings.json')) else {}
    if os.path.exists(os.path.join(d, 'contexts.json')): trees = dict(trees, contexts=J('contexts.json'))
    return manifest, passages, trees, phrases, pics, strings


def pack(d, out_bin, max_syms=textcodec.MAX_SYMS - 1):
    manifest, passages, trees, phrases, pics, strings = load(d)
    S = Strings()
    src_index = {s['id']: i for i, s in enumerate(manifest['sources'])}
    srcs = [S.add(s['title'], raw=True) for s in manifest['sources']]

    # passages: body text + citation string
    pas_index, pasg = {}, []
    for p in passages:
        pas_index[p['id']] = len(pasg)
        pasg.append((S.add(p['text']), S.add(p.get('citation'), raw=True),
                     src_index.get(p.get('source'), NONE)))

    pic_index = {p['id']: i for i, p in enumerate(pics)}
    tree_list = trees['trees']; tree_index = {t['id']: i for i, t in enumerate(tree_list)}
    nodes = sorted(trees['nodes'], key=lambda n: n['id'])
    assert [n['id'] for n in nodes] == list(range(len(nodes))), 'node ids must be dense 0..N-1'

    node_b, npas, choi = bytearray(), [], bytearray()
    for n in nodes:
        first_p, first_c = len(npas), len(choi) // 6
        npas += [pas_index[pid] for pid in n.get('passages', [])]
        npas += [pas_index[pid] for pid in n.get('lookalikes') or []]   # shown after the ID text
        for c in n.get('choices', []):
            choi += struct.pack('<HHBx', c['to'], S.add(c['label'], raw=True), CHOICE_KIND[c.get('kind', 'next')])
        t = tree_list[tree_index[n['tree']]] if n.get('tree') in tree_index else None
        flags = NODE_FLAG_EMERGENCY if (n.get('emergency') or (t and t.get('emergency'))) else 0
        flags |= (NODE_FLAG_SPECIES if n.get('species') else 0) | (NODE_FLAG_NO_MATCH if n.get('no_match') else 0)
        fig = pic_index.get(n.get('figure'), NONE)
        node_b += struct.pack('<HHHBBHHBBH', S.add(n.get('label'), raw=True), first_p, first_c,
                              len(n.get('passages', [])), len(n.get('choices', [])),
                              fig, tree_index.get(n.get('tree'), NONE), flags,
                              len(n.get('lookalikes') or []), NONE)
    choi = bytes(choi)

    tree_b = b''.join(struct.pack('<HHHBx', S.add(t['title'], raw=True), t['root'],
                                  S.add(t.get('shelf'), raw=True), 1 if t.get('emergency') else 0)
                      for t in tree_list)

    n_cmds = max([p['cmd_id'] for p in phrases], default=-1) + 1
    cmds = [NONE] * n_cmds
    for p in phrases:
        assert cmds[p['cmd_id']] in (NONE, p['node']), f'cmd {p["cmd_id"]} maps to two nodes'
        cmds[p['cmd_id']] = p['node']

    # context tags + per-section voice lists (optional chunks NTAG, TAGN, PHRS)
    bits, labels, groups = contexts.parse_vocab(trees.get('contexts'))
    ntag = contexts.pack_masks(contexts.node_masks(tree_list, nodes, bits, groups))
    tagn = contexts.tagn_chunk(labels, lambda s: S.add(s, raw=True))
    phrs = contexts.phrs_chunk(phrases, n_cmds, nodes, tree_list, lambda s: S.add(s, raw=True))

    clash = {}
    for ph in phrases: clash.setdefault((ph.get('spoken') or ph['phrase']).strip().lower(), set()).add(ph['node'])
    clash = sorted(k for k, v in clash.items() if len(v) > 1)
    if clash:
        print(f'warning: {len(clash)} spoken phrases point at more than one node, so voice cannot tell them '
              f'apart when both are in a list: {", ".join(clash[:8])}{" ..." if len(clash) > 8 else ""}', file=sys.stderr)

    # pictures
    picx, picd = [], bytearray()
    for p in pics:
        f = os.path.join(d, p['file'])
        if not os.path.exists(f): f = os.path.join(d, 'pictures', p['file'])
        w, h, codec, data = piccodec.encode_file(f)
        picx.append(len(picd))
        picd += struct.pack('<HHBBHH', w, h, codec, 1 if p.get('pan') else 0,
                            S.add(p.get('caption'), raw=True), S.add(p.get('citation'), raw=True)) + data
        while len(picd) % 4: picd.append(0)
    picx.append(len(picd))

    # fixed UI strings: (name, value) text id pairs, looked up by name once at boot
    strs = b''.join(struct.pack('<HH', S.add(k, raw=True), S.add(v)) for k, v in sorted(strings.items()))

    # text store
    m = textcodec.build(S.items, max_syms)
    assert m['max_depth'] <= 64, 'grammar too deep for the reader stack'
    huff = struct.pack('<16H', *m['count'])
    tidx = struct.pack(f'<{len(m["offsets"])}I', *m['offsets'])

    chunks = [
        (b'SYMS', m['table']), (b'HUFF', huff), (b'TIDX', tidx), (b'TEXT', m['stream']),
        (b'PASG', b''.join(struct.pack('<HHH', *x) for x in pasg)),
        (b'NODE', bytes(node_b)), (b'NPAS', struct.pack(f'<{len(npas)}H', *npas)),
        (b'CHOI', choi), (b'TREE', tree_b), (b'CMDS', struct.pack(f'<{n_cmds}H', *cmds)),
        (b'SRCS', struct.pack(f'<{len(srcs)}H', *srcs)),
        (b'PICX', struct.pack(f'<{len(picx)}I', *picx)), (b'PICD', bytes(picd)),
        (b'STRS', strs),
        (b'FONT', fontpack.build()),
        (b'NTAG', ntag), (b'TAGN', tagn), (b'PHRS', phrs),
    ]
    HDR = 28
    pos = HDR + 12 * len(chunks)
    dirent, body = b'', b''
    for tag, data in chunks:
        dirent += tag + struct.pack('<II', pos + len(body), len(data))
        body += data + b'\0' * (-len(data) % 4)
    home = tree_index.get(trees.get('home_tree'), 0)
    sleep = pic_index.get(trees.get('sleep_picture'), NONE)
    total = pos + len(body)
    hdr = b'CRT1' + struct.pack('<HHII', 1, len(chunks), total, 0) + \
        struct.pack('<HHHHI', home, sleep, len(nodes), len(S.items),
                    int((manifest.get('build_id') or '0')[:8], 16))
    blob = bytearray(hdr + dirent + body)
    struct.pack_into('<I', blob, 12, zlib.crc32(bytes(blob[16:])))
    os.makedirs(os.path.dirname(os.path.abspath(out_bin)), exist_ok=True)
    open(out_bin, 'wb').write(blob)
    json.dump({'passages': pas_index, 'pictures': pic_index, 'trees': tree_index, 'strings': sorted(strings)},
              open(os.path.splitext(out_bin)[0] + '.ids.json', 'w'), indent=0)
    sizes = {tag.decode(): len(data) for tag, data in chunks}
    raw_text = sum(len(t) for t in S.items)
    return blob, m, sizes, raw_text


if __name__ == '__main__':
    blob, m, sizes, raw = pack(sys.argv[1], sys.argv[2])
    print(f'library.bin {len(blob)} bytes; text {raw} raw -> {len(m["stream"])} stream '
          f'+ {sizes["SYMS"] + sizes["HUFF"]} tables ({m["n_alpha"]} chars, {m["n_rules"]} rules, depth {m["max_depth"]})')
    for k, v in sizes.items(): print(f'  {k} {v}')
