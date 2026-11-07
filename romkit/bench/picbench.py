import sys, os, io, numpy as np
sys.path.insert(0, 'romkit')
import piccodec as pc
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import skimage.data as d, skimage.feature as sf, skimage.transform as st

W,H = 250,122
imgs = {}
# 1. synthetic manual-style line diagram (arm + tourniquet + arrows + labels)
im = Image.new('L',(W,H),255); dr = ImageDraw.Draw(im)
dr.line([(10,60),(150,50),(240,62)],fill=0,width=2); dr.line([(10,90),(150,80),(240,88)],fill=0,width=2)
dr.rectangle([95,48,110,92],outline=0,width=2)
for k in range(97,110,3): dr.line([(k,50),(k,90)],fill=0)
dr.line([(103,30),(103,48)],fill=0,width=2); dr.line([(85,30),(121,30)],fill=0,width=3)
dr.polygon([(160,20),(150,30),(170,30)],fill=0); dr.line([(160,30),(160,45)],fill=0)
f = ImageFont.load_default()
dr.text((165,8),"2-3 in above wound",fill=0,font=f); dr.text((20,100),"Figure 2-12. Windlass",fill=0,font=f)
dr.ellipse([180,64,200,80],outline=0)
imgs['line diagram (synthetic)'] = im
# 2. outline drawing from the horse silhouette
hs = d.horse(); edges = sf.canny(st.resize(~hs*1.0,(H*2,int(400*H*2/328)))).astype(np.uint8)
e = Image.fromarray(255-edges*255).resize((int(400*H/328),H)).convert('L')
canvas = Image.new('L',(W,H),255); canvas.paste(e,(40,0)); imgs['outline drawing'] = canvas
# 3. scanned text page
imgs['scanned text'] = Image.fromarray(d.page()).resize((W,H))
# 4. photo crop, dithered (plant-photo stand-in)
imgs['photo dithered'] = Image.fromarray(d.camera()).crop((100,100,100+2*W,100+2*H)).resize((W,H)).convert('1')
imgs['photo threshold'] = Image.fromarray(d.coins()).resize((W,H)).point(lambda v: 255 if v>110 else 0)
# 5. pan image 750x366 line diagram = 3x synthetic
big = Image.new('L',(3*W,3*H),255); 
for i in range(3):
    for j in range(3): big.paste(imgs['line diagram (synthetic)'] if (i+j)%2==0 else imgs['outline drawing'],(i*W,j*H))
imgs['pan 750x366 mosaic'] = big

print(f'{"picture":28s} {"raw":>6s} {"packbits":>8s} {"G4":>6s} {"ctx-arith":>9s}')
for name, im in imgs.items():
    a = pc.to_bits(im); im1 = Image.fromarray(~a).convert('1')
    im1.save(f'pics/{name.split()[0]}.png')
    b = io.BytesIO(); im1.save(b,'TIFF',compression='group4'); b.seek(0)
    g4 = sum(Image.open(b).tag_v2[279])
    raw = pc.raw_rows(a)
    print(f'{name:28s} {len(raw):6d} {len(pc.packbits(raw)):8d} {g4:6d} {len(pc.ctx_encode(a)):9d}')
