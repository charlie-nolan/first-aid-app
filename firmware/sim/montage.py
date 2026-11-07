"""Turn a sim output folder of .pbm screens into one labelled PNG sheet (2x scale)."""
import glob, os, sys
from PIL import Image, ImageDraw
d, out = sys.argv[1], sys.argv[2]
files = sorted(glob.glob(os.path.join(d, '*.pbm')))
cols = 3; W, H = 250 * 2 + 16, 122 * 2 + 30
sheet = Image.new('RGB', (cols * W, ((len(files) + cols - 1) // cols) * H), (225, 228, 220))
dr = ImageDraw.Draw(sheet)
for i, f in enumerate(files):
    raw = open(f, 'rb').read().split(b'\n', 3)
    im = Image.frombytes('1', (256, 122), raw[3]).crop((0, 0, 250, 122))
    im = Image.eval(im.convert('L'), lambda v: 255 - v).resize((500, 244), Image.NEAREST)
    x, y = (i % cols) * W + 8, (i // cols) * H + 22
    sheet.paste(Image.merge('RGB', (im, im, im)), (x, y)); dr.rectangle([x - 1, y - 1, x + 500, y + 244], outline=(90, 90, 90))
    dr.text((x, y - 16), f'{i:02d}  {raw[1][2:].decode()}', fill=(30, 30, 30))
sheet.save(out); print(out, sheet.size)
