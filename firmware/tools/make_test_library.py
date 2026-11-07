"""Build the bench test cartridge (library/library.bin) for the firmware.

This is NOT first-aid content: every page says so. It exists to exercise the
firmware: paging, choices, pictures, look-alikes, two trees, the emergency
flag (battery reserve) and voice commands.

    python3 tools/make_test_library.py            # writes library/library.bin + library/phrases.txt
Needs Python 3 with numpy and Pillow, and ../romkit next to this folder.
"""
import json, os, subprocess, sys
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.dirname(HERE)
ROMKIT = os.path.join(FW, '..', 'romkit')
OUT = os.path.join(FW, 'library', 'test')
os.makedirs(os.path.join(OUT, 'pictures'), exist_ok=True)

NOTE = 'This is a bench test page, not first-aid advice.'
P = []
def passage(pid, text, cite):
    P.append(dict(id=pid, source='BENCH', para=pid, heading='', citation=cite, text=text))
    return pid

passage('B/0', f'## Bench test cartridge\n{NOTE}\n- DOWN turns the page.\n- TALK jumps to the choices, then opens one.\n- BACK goes back.\n- Hold BACK to step the front light.\n- Hold DOWN for the signal menu.',
        'Bench test cartridge, page 0')
lines = '\n'.join(f'Line {i}: the quick brown fox jumps over the lazy dog.' for i in range(1, 31))
passage('B/1', f'## Long page\n{NOTE}\nThis passage is long on purpose, so it takes several pages at every text size.\n{lines}',
        'Bench test cartridge, long page')
passage('B/2', f'## Picture page\n{NOTE}\nThe page before this one is a test picture from the cartridge.', 'Bench test cartridge, picture page')
passage('B/3a', f'## First passage\n{NOTE}\nEach passage shows its own citation at the bottom.', 'Bench test cartridge, passage A')
passage('B/3b', f'## Second passage\n- Bullet one\n- Bullet two, which is long enough to wrap onto a second line on the screen\n- Bullet three', 'Bench test cartridge, passage B')
passage('B/3c', f'## Look-alike passage\n{NOTE}\nLook-alike passages come last on a node.', 'Bench test cartridge, look-alike')
passage('B/4', f'## Not an emergency page\n{NOTE}\nWhen the battery is in its reserve, this page is locked and the emergency tree stays open.', 'Bench test cartridge, second shelf')
passage('B/5', f'## No-match leaf\n{NOTE}', 'Bench test cartridge, no-match')
passage('B/6', f'## Washington only\n{NOTE}\nShown when the state is Washington or Any.', 'Bench test cartridge, WA page')
passage('B/8', f'## Summer-only shelf\n{NOTE}\nThis whole section hides unless the season is summer or Any.', 'Bench test cartridge, summer shelf')
passage('B/7', f'## Winter only\n{NOTE}\nShown when the season is winter or Any.', 'Bench test cartridge, winter page')

nodes = [
    dict(id=0, key='bench/start', tree='bench', label='Bench test', passages=['B/0'], figure=None, choices=[
        dict(label='Long page', to=1, kind='next'), dict(label='Picture page', to=2, kind='next'),
        dict(label='Two passages + look-alike', to=3, kind='next'), dict(label='Second shelf', to=4, kind='next'),
        dict(label='No-match leaf', to=5, kind='next'), dict(label='Washington only', to=6, kind='next'),
        dict(label='Winter only', to=7, kind='next')]),
    dict(id=1, key='bench/long', tree='bench', label='Long page', passages=['B/1'], figure=None,
         choices=[dict(label='Back to start', to=0, kind='next')]),
    dict(id=2, key='bench/picture', tree='bench', label='Picture page', passages=['B/2'], figure='testcard', choices=[]),
    dict(id=3, key='bench/two', tree='bench', label='Two passages', passages=['B/3a', 'B/3b'], lookalikes=['B/3c'], figure=None,
         choices=[dict(label='Yes', to=0, kind='yes'), dict(label='No', to=1, kind='no')]),
    dict(id=4, key='shelf2/start', tree='shelf2', label='Second shelf', passages=['B/4'], figure=None, choices=[]),
    dict(id=5, key='bench/nomatch', tree='bench', label='No-match leaf', passages=['B/5'], figure=None, choices=[], no_match=True),
    dict(id=6, key='shelf2/wa', tree='shelf2', label='Washington only', passages=['B/6'], figure=None, choices=[], tags={'region': ['wa']}),
    dict(id=8, key='summer/start', tree='summer', label='Summer-only shelf', passages=['B/8'], figure=None, choices=[]),
    dict(id=7, key='shelf2/winter', tree='shelf2', label='Winter only', passages=['B/7'], figure=None, choices=[], tags={'season': ['winter']}),
]
trees = dict(trees=[dict(id='bench', title='Bench test (emergency)', shelf='test', root=0, emergency=True),
                    dict(id='shelf2', title='Second shelf (not emergency)', shelf='test', root=4, emergency=False),
                    dict(id='summer', title='Summer-only shelf', shelf='test', root=8, emergency=False, tags={'season': ['summer']})],
             nodes=nodes, home_tree='bench', sleep_picture='sleepcard')

def card(name, w, h, title, sub):
    im = Image.new('1', (w, h), 1); d = ImageDraw.Draw(im)
    f = ImageFont.truetype(os.path.join(ROMKIT, 'fonts', 'LexendDeca-Bold.ttf'), 22)
    g = ImageFont.truetype(os.path.join(ROMKIT, 'fonts', 'LexendDeca-Regular.ttf'), 13)
    d.rectangle([1, 1, w - 2, h - 2], outline=0, width=3)
    for x in range(8, w - 8, 16): d.rectangle([x, h - 18, x + 7, h - 10], fill=0)
    d.text((w // 2, h // 2 - 16), title, font=f, fill=0, anchor='mm')
    d.text((w // 2, h // 2 + 12), sub, font=g, fill=0, anchor='mm')
    im.save(os.path.join(OUT, 'pictures', name + '.png'))

card('testcard', 240, 86, 'TEST PICTURE', 'cartridge picture decode works')
card('sleepcard', 250, 122, 'SLEEP CARD', 'real cartridge: bleeding card')
pics = [dict(id='testcard', figure='testcard', file='testcard.png', w=240, h=86, pan=False, caption='Test picture', source='BENCH', citation='Bench test cartridge, picture'),
        dict(id='sleepcard', figure='sleepcard', file='sleepcard.png', w=250, h=122, pan=False, caption='Sleep card', source='BENCH', citation='Bench test cartridge, sleep card')]

phrases = [dict(cmd_id=0, phrase='bench test', spoken='bench test', node=0),
           dict(cmd_id=1, phrase='long page', spoken='long page', node=1),
           dict(cmd_id=2, phrase='show picture', spoken='show picture', node=2),
           dict(cmd_id=3, phrase='second shelf', spoken='second shelf', node=4),
           dict(cmd_id=4, phrase='washington page', spoken='washington page', node=6),
           dict(cmd_id=5, phrase='summer shelf', spoken='summer shelf', node=8)]

strings = {'WAKE_911': 'Life threat? Call 911 / SOS first.', 'DISCLAIMER': 'Reference only, not medical advice.',
           'NO_MATCH': "No match: don't eat it.", 'PLANT_WARNING': "If unsure, don't eat it."}

J = lambda n, v: json.dump(v, open(os.path.join(OUT, n), 'w'), indent=1)
J('manifest.json', dict(build_id='be4c0001', build_time='', counts={}, errors=[],
                        sources=[dict(id='BENCH', title='Bench test cartridge', edition='1', sha256='', distribution='test')]))
open(os.path.join(OUT, 'passages.jsonl'), 'w').write(''.join(json.dumps(p) + '\n' for p in P))
J('trees.json', trees); J('phrases.json', phrases); J('pictures.json', pics); J('strings.json', strings)
open(os.path.join(FW, 'library', 'phrases.txt'), 'w').write(''.join(f"{p['cmd_id']}\t{p['phrase']}\n" for p in phrases))
lib = os.path.join(FW, 'library', 'library.bin')
subprocess.check_call([sys.executable, os.path.join(ROMKIT, 'pack.py'), OUT, lib])
print('wrote', lib, os.path.getsize(lib), 'bytes')
