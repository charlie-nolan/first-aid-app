import sys, struct, subprocess, copy
sys.path.insert(0,'romkit'); import fontpack, pack
from PIL import Image, ImageDraw
def tid_of(lib, node):
    b=open(lib,'rb').read(); n=struct.unpack_from('<H',b,6)[0]
    d={b[28+12*i:32+12*i]:struct.unpack_from('<II',b,32+12*i) for i in range(n)}
    fp=struct.unpack_from('<H',b,d[b'NODE'][0]+16*node+2)[0]
    pi=struct.unpack_from('<H',b,d[b'NPAS'][0]+2*fp)[0]; return struct.unpack_from('<H',b,d[b'PASG'][0]+6*pi)[0]
def shots(lib, styles, node=78):
    t=tid_of(lib,node); ims=[]
    for s in styles:
        subprocess.run(['./cartcli',lib,'render',str(t),str(s),'0','x.pbm'],check=True,capture_output=True)
        ims.append(Image.open('x.pbm').crop((0,0,250,122)).convert('L'))
    return ims
def sheet(rows, out, labels):
    pad=10; W=3*250+4*pad; H=len(rows)*(122+pad+14)+pad
    c=Image.new('L',(W,H),170); d=ImageDraw.Draw(c)
    for r,(ims,lab) in enumerate(zip(rows,labels)):
        y=pad+r*(122+pad+14); d.text((pad,y),lab,fill=0)
        for i,im in enumerate(ims): c.paste(im,(pad+i*(250+pad),y+14))
    c.resize((W*2,H*2),Image.NEAREST).save(out)
mode=sys.argv[1]
if mode=='sweep':
    rows=[];labels=[]
    for th in (20,40,64,96):
        fontpack.DEFAULT['threshold']=th
        pack.pack('fixture','sw.bin'); rows.append(shots('sw.bin',[0,3,4])); labels.append(f'threshold {th}: Lexend small | Bitter small | Bitter medium')
    sheet(rows,'sweep.png',labels)
else:
    pack.pack('fixture','out/library.bin')
    sheet([shots('out/library.bin',[0,1,2]), shots('out/library.bin',[3,4,5])],'fonts_preview2.png',['Lexend Deca: small 12 px | medium 15 px | large 19 px','Bitter: small 12 px | medium 15 px | large 19 px'])
