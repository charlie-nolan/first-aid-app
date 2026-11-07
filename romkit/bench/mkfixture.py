"""Build a fake pipeline intermediate from the stand-in corpus (for tests only)."""
import glob, json, os, re, shutil
OUT = 'fixture'; shutil.rmtree(OUT, ignore_errors=True); os.makedirs(OUT + '/pictures')
tr = str.maketrans({'–': '-', '—': '-', '≈': '~', '×': 'x', '½': '1/2', '¾': '3/4', '°': ' degrees'})
passages, trees, nodes, phrases = [], [], [], []
for f in sorted(glob.glob('corpus/ts_*.txt')):
    ch = f.split('_')[1][:-4]
    txt = open(f, encoding='utf-8').read().translate(tr).replace('**', '')
    parts = [p.strip() for p in re.split(r'\n(?=#{2,3} )', txt) if len(p.strip()) > 20]
    first = len(nodes)
    trees.append(dict(id=ch, title=ch.title(), shelf='survival', root=first, emergency=ch == 'medical'))
    for k, p in enumerate(parts):
        kw = re.findall(r'<!-- K: (.*?) -->', p)
        body = re.sub(r'<!--.*?-->\n?', '', p).strip()
        m = re.match(r'#+ (.*)', body); label = m.group(1)[:40] if m else ch.title()
        body = re.sub(r'^###? ', '## ', body, flags=re.M)
        pid = f'TS-{ch}/{k}'
        passages.append(dict(id=pid, source='TS', para=str(k), heading=label, citation=f'Trail Sense guide, {ch} {k}', text=body))
        nid = len(nodes)
        ch_ = [dict(label='Next', to=nid + 1, kind='next')] if k + 1 < len(parts) else []
        nodes.append(dict(id=nid, key=f'{ch}/{k}', tree=ch, label=label, passages=[pid], figure=None, choices=ch_))
        for w in (kw[0].split(', ')[:3] if kw else []):
            phrases.append(dict(cmd_id=len(phrases), phrase=w.replace('-', ' '), node=nid))
pics = []
for i, f in enumerate(sorted(glob.glob('pics/*.png'))):
    shutil.copy(f, OUT + '/pictures/'); b = os.path.basename(f)
    pics.append(dict(id=b[:-4], file=b, pan='pan' in b, caption=b[:-4], source='TS', citation='test'))
    nodes[i]['figure'] = b[:-4]
json.dump(dict(build='fixture', sources=[dict(id='TS', title='Trail Sense survival guide (test stand-in)', edition='main', sha256='', distribution='MIT')]), open(OUT + '/manifest.json', 'w'))
open(OUT + '/passages.jsonl', 'w').write(''.join(json.dumps(p) + '\n' for p in passages))
json.dump(dict(trees=trees, nodes=nodes, home_tree='medical', sleep_picture=pics[0]['id']), open(OUT + '/trees.json', 'w'))
json.dump(phrases, open(OUT + '/phrases.json', 'w')); json.dump(pics, open(OUT + '/pictures.json', 'w'))
print(len(passages), 'passages', len(nodes), 'nodes', len(phrases), 'phrases', len(pics), 'pictures')
