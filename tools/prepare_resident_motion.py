"""Recover unclipped sources into a shared, padded candidate canvas.

Writes candidates only. Source pixels and original installed packs are retained.
Visual approval and installation are separate from these geometry checks.
"""
import json
import statistics
import shutil
from pathlib import Path
import cv2
import numpy as np
from PIL import Image,ImageDraw,ImageFont
from tools.install_video_idle_actions import body_span
from core.avatar_review import frames_digest

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runtime/resident-motion-20261108/candidate'
CANVAS=(320,416)
BASELINE=408
BODY=282*245/227


def source_frame(path,name):
    with Image.open(path) as source:image=source.convert('RGBA')
    pixels=np.array(image);alpha=pixels[:,:,3]
    if name in ('doze','heart','pout','laugh'):
        hsv=cv2.cvtColor(pixels[:,:,:3],cv2.COLOR_RGB2HSV)
        feet=(alpha>=128)&(hsv[:,:,0]>15)&(hsv[:,:,0]<40)&(hsv[:,:,1]>100)&(hsv[:,:,2]>120)
        feet[:int(image.height*.65)]=False
        rows=np.where(feet.any(axis=1))[0]
        if rows.size:pixels[min(image.height,int(rows.max())+3):,:,3]=0
    return Image.fromarray(pixels)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    records={};previews=[]
    for name in ('doze','heart','pout','laugh'):
        source=(ROOT/'runtime/idle-action-review-20261102'/(name+'-v1')/'frames'
            if name in ('doze','heart') else ROOT/'runtime/resident-motion-20261108/repaired-source'/name/'frames')
        paths=sorted(source.glob('*.png'));spans=[];centers=[];boxes=[];floors=[]
        if not 200<=len(paths)<=300:raise ValueError(name+': missing bounded, uncut source frames')
        for path in paths:
            image=source_frame(path,name);pixels=np.array(image);mask=pixels[:,:,3]>=128
            pixels[~mask,:3]=0
            span,cx=body_span(cv2.cvtColor(pixels[:,:,:3],cv2.COLOR_RGB2BGR))
            spans.append(span);centers.append(cx)
            ys,xs=np.where(mask);boxes.append((xs.min(),ys.min(),xs.max()+1,ys.max()+1));floors.append(ys.max()+1)
        scale=BODY/statistics.median(spans)
        center=statistics.median(centers);floor=statistics.median(floors)
        left=CANVAS[0]/2-center*scale;top=BASELINE-floor*scale
        target=OUT/name/'frames';target.mkdir(parents=True,exist_ok=True)
        clipped=[];sampled=[];geometry=[]
        for index,path in enumerate(paths):
            image=source_frame(path,name)
            image=image.resize((round(image.width*scale),round(image.height*scale)),Image.Resampling.LANCZOS)
            frame=Image.new('RGBA',CANVAS);frame.alpha_composite(image,(round(left),round(top)))
            a=np.array(frame);ys,xs=np.where(a[:,:,3]>=128)
            if xs.min()<3 or xs.max()>CANVAS[0]-4 or ys.min()<2 or ys.max()>CANVAS[1]-2:clipped.append(index)
            rgb=a[:,:,:3].copy();rgb[a[:,:,3]<128]=0
            actual,cx=body_span(cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR));geometry.append(actual)
            frame.save(target/f'{index:04}.png')
            if index in (0,12,len(paths)//2,len(paths)-13,len(paths)-1):sampled.append(frame)
        if clipped:raise RuntimeError(name+': candidate touches edge '+str(clipped[:8]))
        exit_indices=[]
        if name=='laugh':
            # The supplied clip ends mid-wave. Match an existing raised-wing
            # intro pose, then play those original frames backwards to settle.
            # No invented pixels, temporal blending or motion interpolation.
            def premult(path):
                a=np.array(Image.open(path).convert('RGBA')).astype(np.float32)/255
                a[:,:,:3]*=a[:,:,3:];return a
            last=premult(target/f'{len(paths)-1:04}.png')
            score,index=min((float(np.abs(premult(target/f'{i:04}.png')-last).mean()),i)
                for i in range(1,19))
            if score>.055:raise RuntimeError('laugh: no suitable existing settling pose')
            exit_indices=list(range(index,-1,-1))
            for offset,source_index in enumerate(exit_indices):
                shutil.copyfile(target/f'{source_index:04}.png',target/f'{len(paths)+offset:04}.png')
        metadata=dict(name=name,frames=len(paths)+len(exit_indices),source_frames=len(paths),
            settling_source_indices=exit_indices,native_fps=24.,canvas=list(CANVAS),
            body_span=statistics.median(geometry),reference_body_span=BODY,baseline=BASELINE,
            source=str(source.relative_to(ROOT)),scale=scale,offset=[round(left),round(top)],
            edge_clipped_frames=0,frame_sha256=frames_digest(target),source_pixels_preserved=True,
            reference_width=245,native_motion=True)
        (target.parent/'geometry.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
        records[name]=metadata;previews.append((name,sampled))
    sheet=Image.new('RGB',(5*340,4*450),'#e9edf2');draw=ImageDraw.Draw(sheet)
    font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',18)
    for row,(name,samples) in enumerate(previews):
        for col,frame in enumerate(samples):
            sheet.paste(frame,(col*340+10,row*450+36),frame)
            draw.text((col*340+12,row*450+6),name+' / '+str(col),font=font,fill='#223344')
    sheet.save(OUT/'contact.png')
    (OUT/'geometry.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    manifest=dict(schema=2,padded_canvas=True,clips=[dict(name=name,frames=name+'/frames',
        native_fps=24.,reference_width=245,weight=.8 if name=='doze' else 1.) for name in records])
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({name:dict(body_span=value['body_span'],edge_clipped_frames=0,frames=value['frames'])
        for name,value in records.items()}))


if __name__=='__main__':main()
