"""Bounded offline matte probe: four stills, baseline plus two row-fit masks.

Only alpha is changed; RGB always comes verbatim from the input. No model,
network, generated pixels, mirroring, source modification or approval changes.
"""
import ast
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'runtime/resident-motion-20261108/matte-probe'
NAMES = ('v1_first', 'v1_mid', 'v2_first', 'v2_mid')


def original_matte():
    source = (ROOT / 'tools/install_video_idle_actions.py').read_text(encoding='utf-8')
    function = next(n for n in ast.parse(source).body
                    if isinstance(n, ast.FunctionDef) and n.name == 'matte')
    namespace = {'cv2': cv2, 'np': np}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<old-matte-only>', 'exec'), namespace)
    return namespace['matte']


def row_matte(bgr, threshold):
    h, w = bgr.shape[:2]
    # Border strips contain backdrop; fit its horizontal gradient separately
    # on each row, then smooth only the fitted background along the vertical.
    left = np.median(bgr[:, :16].astype(np.float32), axis=1)
    right = np.median(bgr[:, -16:].astype(np.float32), axis=1)
    left = cv2.GaussianBlur(left[:, None, :], (1, 21), 0)[:, 0]
    right = cv2.GaussianBlur(right[:, None, :], (1, 21), 0)[:, 0]
    t = np.linspace(0, 1, w, dtype=np.float32)[None, :, None]
    background = left[:, None, :] * (1-t) + right[:, None, :] * t
    delta = np.sqrt(np.mean((bgr.astype(np.float32)-background)**2, axis=2))
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    seed = ((delta > threshold) | (hsv[:, :, 1] > 55)).astype(np.uint8)*255
    feet = ((hsv[:, :, 0] > 10) & (hsv[:, :, 0] < 40)
            & (hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 110))
    # Restrict the floor estimate to the feet region, excluding beak/accessories
    # and reflected yellow below the floor. Use the first large lower component.
    feet[:int(h*.7)] = False
    count, labels, stats, _ = cv2.connectedComponentsWithStats(feet.astype(np.uint8), 8)
    candidates = [i for i in range(1, count) if stats[i, 4] > 100]
    if not candidates:
        raise ValueError('No reliable feet/floor anchor')
    top = min(stats[i, 1] for i in candidates)
    feet_ids = [i for i in candidates if stats[i, 1] <= top+12]
    floor = max(stats[i, 1]+stats[i, 3] for i in feet_ids)
    # Row colour difference also detects the contact shadow. Around the soles,
    # keep dark material or coloured feet, not desaturated bright ground.
    ground_start = max(0, int(top)-6)
    material = ((hsv[:, :, 2] < 95) | (hsv[:, :, 1] > 55))
    seed[ground_start:] *= material[ground_start:].astype(np.uint8)
    seed[floor+2:] = 0
    seed = cv2.morphologyEx(seed, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(seed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    mask = np.zeros_like(seed)
    for contour in contours:
        if cv2.contourArea(contour) >= 100:
            cv2.drawContours(mask, [contour], -1, 255, -1)
    # No erosion: it was another way to lose thin hair/hood edges.
    mask = cv2.GaussianBlur(mask, (3, 3), .45)
    mask[floor+2:] = 0
    return mask, floor


def preview(rgba):
    h, w = rgba.shape[:2]
    yy, xx = np.indices((h, w))
    tile = ((xx//20+yy//20) % 2)[..., None]
    bg = np.where(tile, np.array([68, 80, 105]), np.array([115, 127, 150])).astype(np.uint8)
    a = rgba[:, :, 3:4].astype(np.float32)/255
    return Image.fromarray(np.rint(rgba[:, :, :3]*a+bg*(1-a)).astype(np.uint8))


def main():
    started = time.monotonic()
    OUT.mkdir(parents=True, exist_ok=True)
    old = original_matte()
    full = Image.new('RGB', (960, 4*350), 'white')
    heads = Image.new('RGB', (1120, 4*310), 'white')
    fd, hd = ImageDraw.Draw(full), ImageDraw.Draw(heads)
    results = []
    for row, name in enumerate(NAMES):
        if time.monotonic()-started > 65:
            raise TimeoutError('Probe runtime exceeded 65 seconds')
        rgb = np.array(Image.open(ROOT / 'runtime/video-intake' / (name+'.png')).convert('RGB'))
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        baseline = old(bgr)[:, :, 3]
        a18, floor = row_matte(bgr, 18)
        a10, _ = row_matte(bgr, 10)
        columns = [('original', None), ('old', baseline), ('row18', a18), ('row10', a10)]
        h, w = rgb.shape[:2]
        roi = (int(w*.55), int(h*.05), int(w*.94), int(h*.56))
        record = {'sample': name, 'floor_y': int(floor), 'size': [w, h]}
        for col, (method, alpha) in enumerate(columns):
            if alpha is None:
                rendered = Image.fromarray(rgb)
            else:
                rgba = np.dstack([rgb, alpha])
                assert np.array_equal(rgba[:, :, :3], rgb)
                Image.fromarray(rgba).save(OUT / f'{name}_{method}.png')
                rendered = preview(rgba)
                x0, y0, x1, y1 = roi
                gained = ((alpha > 128) & (baseline < 128))[y0:y1, x0:x1]
                record[method] = {'right_head_recovered_pixels': int(gained.sum()),
                                  'alpha_pixels_below_floor_plus_2': int((alpha[floor+2:] > 0).sum())}
            full.paste(rendered.resize((240, 320)), (col*240, row*350+27))
            fd.text((col*240+5, row*350+5), f'{name} / {method}', fill='black')
            crop = rendered.crop(roi).resize((280, 280))
            heads.paste(crop, (col*280, row*310+27))
            hd.text((col*280+5, row*310+5), f'{name} / {method}', fill='black')
        results.append(record)
    full.save(OUT / 'comparison_full.png')
    heads.save(OUT / 'comparison_right_head.png')
    report = {'methods': ['old', 'row18', 'row10'], 'samples': results,
              'elapsed_seconds': round(time.monotonic()-started, 3),
              'rgb_unchanged': True, 'model_used': False,
              'limits': 'Single frames only. Border fit and foot floor are assumptions; no temporal validation.'}
    (OUT / 'metrics.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


def hybrid_probe():
    """Four stills only: row18 head, unchanged old lower-body alpha."""
    OUT.mkdir(parents=True, exist_ok=True)
    old = original_matte()
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 23)
    small = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 18)
    sheet = Image.new('RGB', (720, 94+4*514), 'white')
    draw = ImageDraw.Draw(sheet)
    draw.text((16, 8), '静态试样｜仅4张｜整视频待源文件', font=font, fill='#8b330c')
    draw.text((120, 47), '原来', font=font, fill='black')
    draw.text((430, 47), '边缘修复试样', font=font, fill='black')
    records = []
    labels = ('pout 首帧', 'pout 中间帧', 'laugh 首帧', 'laugh 中间帧')
    for row, (name, label) in enumerate(zip(NAMES, labels)):
        rgb = np.array(Image.open(ROOT/'runtime/video-intake'/(name+'.png')).convert('RGB'))
        baseline = old(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))[:, :, 3]
        row18 = np.array(Image.open(OUT/(name+'_row18.png')).convert('RGBA'))[:, :, 3]
        h, w = baseline.shape
        boundary = round(.58*h)
        alpha = baseline.copy()
        alpha[:boundary-1] = row18[:boundary-1]
        # Exactly three handoff rows; RGB never participates in the blend.
        for y, weight in zip(range(boundary-1, boundary+2), (1., .5, 0.)):
            alpha[y] = np.rint(weight*row18[y].astype(float)+(1-weight)*baseline[y]).astype(np.uint8)
        assert np.array_equal(alpha[boundary+2:], baseline[boundary+2:])
        rgba = np.dstack([rgb, alpha])
        assert np.array_equal(rgba[:, :, :3], rgb)
        Image.fromarray(rgba).save(OUT/(name+'_hybrid.png'))
        y = 94+row*514
        draw.text((12, y), label, font=small, fill='black')
        draw.text((372, y), label, font=small, fill='black')
        sheet.paste(preview(np.dstack([rgb, baseline])).resize((360, 480)), (0, y+30))
        sheet.paste(preview(rgba).resize((360, 480)), (360, y+30))
        records.append({'sample': name, 'boundary_y': boundary,
                        'three_alpha_handoff_rows': [boundary-1, boundary, boundary+1],
                        'lower_alpha_exactly_old': True, 'rgb_exactly_source': True})
    target = OUT/'hybrid_静态试样_原来与边缘修复.png'
    sheet.save(target)
    (OUT/'hybrid_metrics.json').write_text(json.dumps({'status': '静态试样，整视频待源文件',
        'samples': records}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(str(target))


if __name__ == '__main__':
    hybrid_probe() if '--hybrid' in sys.argv else main()
