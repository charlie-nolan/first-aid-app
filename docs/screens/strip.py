"""Render chosen sim frames (.pbm) as one captioned strip at 2x, e-ink look."""
import sys, json
from PIL import Image, ImageDraw, ImageFont
out, title, items = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])  # [[pbm, caption], ...]
try:
    F = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 18); FB = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 22)
except OSError:
    F = FB = ImageFont.load_default()
W, H, pad = 500, 244, 20
cols = min(3, len(items)); rows = (len(items) + cols - 1) // cols
sheet = Image.new('RGB', (cols * (W + pad) + pad, 50 + rows * (H + 52) + 10), (236, 236, 230))
d = ImageDraw.Draw(sheet); d.text((pad, 14), title, font=FB, fill=(25, 25, 25))
for i, (f, cap) in enumerate(items):
    raw = open(f, 'rb').read().split(b'\n', 3)
    im = Image.frombytes('1', (256, 122), raw[3]).crop((0, 0, 250, 122))
    im = Image.eval(im.convert('L'), lambda v: 255 - v).resize((W, H), Image.NEAREST)
    im = Image.eval(im, lambda v: 40 if v < 128 else 232)
    x = pad + (i % cols) * (W + pad); y = 50 + (i // cols) * (H + 52) + 26
    d.text((x, y - 24), f'{i + 1}. {cap}', font=F, fill=(40, 40, 40))
    sheet.paste(Image.merge('RGB', (im, im, im)), (x, y)); d.rectangle([x - 2, y - 2, x + W + 1, y + H + 1], outline=(70, 70, 70), width=2)
sheet.save(out, optimize=True); print(out, sheet.size)
