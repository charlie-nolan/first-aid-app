"""Context tags (state / season / activity) and per-section voice lists.

Tags are 96 bits per node (three u32 words, little-endian):
  bits  0-63  region: by default the 50 US states + DC by postal code, alphabetical
  bits 64-67  season
  bits 68-75  activity
Content with no region tag is "global / standard": it shows in every state.
The device keeps the chosen state (and season, activity) until the person changes it.

Input (all optional, in the intermediate):
  contexts.json, or a "contexts" key in trees.json:
      {"region": [...], "season": [...], "activity": [...],   # APPEND ONLY: bit = position
       "groups": {"pnw": ["wa", "or", "id"]}}                  # shorthands for region tags
      Entries are id strings or {"id": "wa", "label": "Washington"}. Leaving "region" out
      uses the built-in state list below.
  trees[].tags / nodes[].tags: {"region": ["wa", "or"], "season": ["winter"]}
      Region tags may use groups, "all", and "-fl" to exclude ({"region": ["all", "-fl"]}).
      A node without a dimension inherits its tree's.
  nodes[].emergency: true  -> never filtered.   phrases[].always: true -> in every voice list.
"""
import struct

STATES = [('ak', 'Alaska'), ('al', 'Alabama'), ('ar', 'Arkansas'), ('az', 'Arizona'), ('ca', 'California'),
          ('co', 'Colorado'), ('ct', 'Connecticut'), ('dc', 'District of Columbia'), ('de', 'Delaware'),
          ('fl', 'Florida'), ('ga', 'Georgia'), ('hi', 'Hawaii'), ('ia', 'Iowa'), ('id', 'Idaho'),
          ('il', 'Illinois'), ('in', 'Indiana'), ('ks', 'Kansas'), ('ky', 'Kentucky'), ('la', 'Louisiana'),
          ('ma', 'Massachusetts'), ('md', 'Maryland'), ('me', 'Maine'), ('mi', 'Michigan'), ('mn', 'Minnesota'),
          ('mo', 'Missouri'), ('ms', 'Mississippi'), ('mt', 'Montana'), ('nc', 'North Carolina'),
          ('nd', 'North Dakota'), ('ne', 'Nebraska'), ('nh', 'New Hampshire'), ('nj', 'New Jersey'),
          ('nm', 'New Mexico'), ('nv', 'Nevada'), ('ny', 'New York'), ('oh', 'Ohio'), ('ok', 'Oklahoma'),
          ('or', 'Oregon'), ('pa', 'Pennsylvania'), ('ri', 'Rhode Island'), ('sc', 'South Carolina'),
          ('sd', 'South Dakota'), ('tn', 'Tennessee'), ('tx', 'Texas'), ('ut', 'Utah'), ('va', 'Virginia'),
          ('vt', 'Vermont'), ('wa', 'Washington'), ('wi', 'Wisconsin'), ('wv', 'West Virginia'), ('wy', 'Wyoming')]
DEFAULTS = {'region': [{'id': i, 'label': l} for i, l in STATES],
            'season': ['spring', 'summer', 'fall', 'winter'],
            'activity': ['hiking', 'water', 'vehicle', 'camp']}
DIMS = (('region', 0, 64), ('season', 64, 4), ('activity', 68, 8))   # name, shift, width
NBITS = 96
NONE = 0xFFFF
PHRASE_ALWAYS, PHRASE_ENTRY = 1, 2        # entry = phrase that opens a section (tree root)


def parse_vocab(spec):
    """-> bits {dim: {tag: bit}}, labels {dim: [(id, label)]}, groups {name: [tag ids]}"""
    spec = dict(spec or {})
    groups = spec.pop('groups', {})
    for k in spec:
        assert k in DEFAULTS, f'unknown context dimension {k!r} (known: {list(DEFAULTS)})'
    bits, labels = {}, {}
    for name, shift, width in DIMS:
        entries = spec.get(name, DEFAULTS[name])
        assert len(entries) <= width, f'too many {name} tags ({len(entries)} > {width})'
        lst = [(e, e) if isinstance(e, str) else (e['id'], e.get('label', e['id'])) for e in entries]
        assert len({i for i, _ in lst}) == len(lst), f'duplicate {name} tag'
        bits[name] = {tid: shift + k for k, (tid, _) in enumerate(lst)}
        labels[name] = lst
    for g, members in groups.items():
        for m in members:
            assert m in bits['region'], f'group {g!r}: unknown region {m!r}'
    return bits, labels, groups


def mask(tags, bits, groups, where):
    m = 0
    for dim, ids in (tags or {}).items():
        assert dim in bits, f'{where}: unknown context dimension {dim!r}'
        inc, exc = 0, 0
        for t in ([ids] if isinstance(ids, str) else ids):
            neg = t.startswith('-'); t = t.lstrip('-')
            if t == 'all': names = list(bits[dim])
            elif dim == 'region' and t in groups: names = groups[t]
            else:
                assert t in bits[dim], f'{where}: tag {t!r} is not declared under contexts.{dim}'
                names = [t]
            b = sum(1 << bits[dim][n] for n in names)
            if neg: exc |= b
            else: inc |= b
        if exc and not inc: inc = sum(1 << v for v in bits[dim].values())   # "-fl" alone = all but FL
        m |= inc & ~exc
    return m


def node_masks(trees, nodes, bits, groups):
    tmask = {t['id']: mask(t.get('tags'), bits, groups, f"tree {t['id']}") for t in trees}
    fields = [((1 << w) - 1) << s for _, s, w in DIMS]
    out = []
    for n in nodes:
        own = mask(n.get('tags'), bits, groups, f"node {n['id']}")
        inh = tmask.get(n.get('tree'), 0)
        out.append(sum((own & f) if own & f else (inh & f) for f in fields))
    return out


def pack_masks(masks):
    return b''.join(struct.pack('<III', m & 0xFFFFFFFF, (m >> 32) & 0xFFFFFFFF, m >> 64) for m in masks)


def pack_sparse(masks):
    """Only tagged nodes cost anything. TAGP: palette of distinct masks (12 B each).
    TAGI: u32 (node << 16 | palette index) per tagged node, sorted by node."""
    pal, idx = [], {}
    entries = []
    for node, m in enumerate(masks):
        if not m: continue
        if m not in idx: idx[m] = len(pal); pal.append(m)
        entries.append(node << 16 | idx[m])
    assert len(pal) < 65536
    return pack_masks(pal), struct.pack(f'<{len(entries)}I', *entries)


def tagn_chunk(labels, add_text):
    """u8 n_dims, 3 pad; dims[n] {u16 name_text, u8 shift, u8 width}; u16 label_text[96]"""
    b = struct.pack('<B3x', len(DIMS))
    lab = [NONE] * NBITS
    for name, shift, width in DIMS:
        b += struct.pack('<HBB', add_text(name), shift, width)
        for k, (_, label) in enumerate(labels[name]):
            lab[shift + k] = add_text(label)
    return b + struct.pack(f'<{NBITS}H', *lab)


def phrs_chunk(phrases, n_cmds, nodes, trees, add_text):
    """6 B per cmd id: u16 spoken_text, u16 node, u8 flags, u8 pad. Index = ESP-SR command id."""
    roots = {t['root'] for t in trees}
    emerg_trees = {t['id'] for t in trees if t.get('emergency')}
    rows = [(NONE, NONE, 0)] * n_cmds
    for p in phrases:
        node = nodes[p['node']]
        flags = PHRASE_ALWAYS if (p.get('always') or (node.get('tree') in emerg_trees and p['node'] in roots)) else 0
        flags |= PHRASE_ENTRY if p['node'] in roots else 0
        rows[p['cmd_id']] = (add_text(p.get('spoken') or p['phrase']), p['node'], flags)
    return b''.join(struct.pack('<HHBx', *r) for r in rows)
