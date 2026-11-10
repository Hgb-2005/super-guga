"""Bounded offline geometry probe, not an installable animation pack.

Only writes runtime/resident-motion-20261108/bridge-probe. No model/network,
RIFE, source edits or alpha crossfade. Exact endpoints are lossless PNGs;
MP4 is a lossy, composited review artifact and cannot promise exact pixels.
"""
import json
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'runtime/resident-motion-20261108/bridge-probe'
W, H, N = 320, 416, 12


def load(path):
    return np.array(Image.open(path).convert('RGBA'))


def sdf(alpha):
    mask = (alpha >= 128).astype(np.uint8)
    return cv2.distanceTransform(mask, cv2.DIST_L2, 5) - cv2.distanceTransform(1-mask, cv2.DIST_L2, 5)


def composite(rgba):
    yy, xx = np.indices((H, W))
    bg = np.where(((xx//16+yy//16) % 2)[..., None], [203, 213, 224], [235, 240, 246])
    a = rgba[..., 3:4]/255.
    return np.rint(rgba[..., :3]*a+bg*(1-a)).astype(np.uint8)


def base_frame():
    rgba = load(ROOT/'resources/expressions/normal_transparent.png')
    # Use the same beak-to-sole definition as the candidate preparation tool,
    # with no import of tools that may have setup/write side effects.
    rgb = rgba[..., :3].copy()
    rgb[rgba[..., 3] < 128] = 0
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    yellow = ((hsv[..., 0] > 15) & (hsv[..., 0] < 40) &
              (hsv[..., 1] > 110) & (hsv[..., 2] > 140)).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(yellow, 8)
    beak = max((i for i in range(1, count) if stats[i, 4] > 40 and stats[i, 1] < rgba.shape[0]/2),
               key=lambda i: stats[i, 4])
    beak_y = stats[beak, 1]+stats[beak, 3]/2
    span = np.where(yellow)[0].max()-beak_y
    scale = 304.3612334801762/span
    resized = Image.fromarray(rgba).resize((round(rgba.shape[1]*scale), round(rgba.shape[0]*scale)), Image.Resampling.LANCZOS)
    a = np.array(resized)[..., 3]
    ys, xs = np.where(a >= 128)
    offset = (round(160-(xs.min()+xs.max()+1)/2), 408-int(ys.max()+1))
    canvas = Image.new('RGBA', (W, H))
    canvas.alpha_composite(resized, offset)
    return np.array(canvas), dict(scale=float(scale), offset=list(offset), body_span_target=304.3612334801762,
                                  center_x=160, sole_exclusive_y=408)


def flow_image(rgba):
    # Composite only to give the flow solver defined background texture.
    gray = cv2.cvtColor(composite(rgba), cv2.COLOR_RGB2GRAY)
    distance = np.clip(sdf(rgba[..., 3]), -20, 20)
    return np.clip(gray*.8+(distance+20)*1.25, 0, 255).astype(np.uint8)


def build_bridge(a, b, count=12):
    """Experimental correspondence warp; returns frames and diagnostic metrics.

    Not approved for runtime: open/closed mouth correspondence can dissolve.
    """
    if count < 2 or count > 60:
        raise ValueError('bounded count must be in [2, 60]')
    gray_a, gray_b = flow_image(a), flow_image(b)
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    dis.setUseSpatialPropagation(True)
    forward = dis.calc(gray_a, gray_b, None)
    backward = dis.calc(gray_b, gray_a, None)
    yy, xx = np.indices((H, W), dtype=np.float32)
    grid = np.dstack((xx, yy))

    def remap(value, coords):
        return cv2.remap(value, coords[..., 0], coords[..., 1], cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_CONSTANT, borderValue=0)

    def inverse(flow, amount):
        # Invert x + amount*flow(x), rather than sampling the other frame at x.
        coords = grid.copy()
        for _ in range(5):
            coords = grid-amount*remap(flow, coords)
        return coords

    distance_a, distance_b = sdf(a[..., 3]), sdf(b[..., 3])
    frames, records = [], []
    for index in range(count):
        u = index/(count-1)
        t = u*u*(3-2*u)
        if index == 0:
            frame = a.copy()
        elif index == count-1:
            frame = b.copy()
        else:
            ma, mb = inverse(forward, t), inverse(backward, 1-t)
            # Transition correspondence-aligned RGB, never two whole silhouettes.
            ca, cb = remap(a[..., :3].astype(np.float32), ma), remap(b[..., :3].astype(np.float32), mb)
            da, db = remap(distance_a, ma), remap(distance_b, mb)
            distance = (1-t)*da+t*db
            alpha = np.clip(distance+.5, 0, 1)
            # If an occluded correspondence is outside its source, favour the
            # source that actually owns this point instead of pulling black in.
            wa, wb = (1-t)*np.clip(da+1, 0, 1), t*np.clip(db+1, 0, 1)
            rgb = (ca*wa[..., None]+cb*wb[..., None])/np.maximum((wa+wb)[..., None], 1e-5)
            frame = np.dstack((np.clip(np.rint(rgb), 0, 255).astype(np.uint8), np.rint(alpha*255).astype(np.uint8)))
            frame[frame[..., 3] == 0, :3] = 0
        mask = (frame[..., 3] >= 128).astype(np.uint8)
        c, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        significant = [int(stats[k, 4]) for k in range(1, c) if stats[k, 4] >= 25]
        record = dict(index=index, smoothstep=float(t), significant_components=len(significant),
                      component_areas=significant, alpha_area=int(mask.sum()))
        if frames:
            record['adjacent_rgb_mae'] = float(np.abs(composite(frame).astype(np.float32)-composite(frames[-1])).mean())
        records.append(record)
        frames.append(frame)
    residual = forward+remap(backward, grid+forward)
    mask = a[..., 3] >= 128
    return frames, dict(frames=records, forward_backward_error_median=float(np.median(np.linalg.norm(residual[mask], axis=1))),
                        endpoint_first_equal=bool(np.array_equal(frames[0], a)),
                        endpoint_last_equal=bool(np.array_equal(frames[-1], b)),
                        endpoint_max_error=[int(np.abs(frames[0].astype(int)-a).max()), int(np.abs(frames[-1].astype(int)-b).max())])


def main():
    started = time.monotonic()
    cv2.setNumThreads(2)
    OUT.mkdir(parents=True, exist_ok=True)
    base, placement = base_frame()
    Image.fromarray(base).save(OUT/'base.png')
    sheet = Image.new('RGB', (W*5, (H+28)*3), 'white')
    draw = ImageDraw.Draw(sheet)
    metrics = dict(method='bidirectional DIS, inverse warp, smoothstep, warped signed-distance silhouette',
                   base_placement=placement, adopt=False,
                   review='Not adopted: pout intermediate eyes/mouth visibly dissolve and ghost. Heart outline cleaner. Doze added as harder geometry-only case. Candidate may be rebuilding; endpoints are this run snapshots.', bridges={})
    combined = cv2.VideoWriter(str(OUT/'bridges.mp4'), cv2.VideoWriter_fourcc(*'mp4v'), 24, (W*3, H))
    if not combined.isOpened():
        raise RuntimeError('MP4 encoder unavailable')
    sequences = []
    for row, name in enumerate(('heart', 'pout', 'doze')):
        if time.monotonic()-started > 100:
            raise RuntimeError('Probe budget exceeded; no further interpolation')
        endpoint_path = ROOT/f'runtime/resident-motion-20261108/candidate/{name}/frames/0000.png'
        endpoint = load(endpoint_path)
        if endpoint.shape != base.shape:
            raise RuntimeError('Unexpected endpoint geometry')
        frames, record = build_bridge(base, endpoint, N)
        folder = OUT/name
        folder.mkdir(exist_ok=True)
        for i, frame in enumerate(frames):
            Image.fromarray(frame).save(folder/f'{i:04}.png')
        # Verify the *saved* lossless images, not only the in-memory arrays.
        record['saved_first_equal'] = bool(np.array_equal(load(folder/'0000.png'), base))
        record['saved_last_equal'] = bool(np.array_equal(load(folder/'0011.png'), endpoint))
        record['source'] = str(endpoint_path.relative_to(ROOT))
        record['limitation'] = 'pout candidate head boundary is damaged; geometry probe only' if name == 'pout' else ''
        metrics['bridges'][name] = record
        sequences.append(frames)
        for col, index in enumerate((0, 3, 6, 8, 11)):
            sheet.paste(Image.fromarray(composite(frames[index])), (col*W, row*(H+28)+28))
            draw.text((col*W+6, row*(H+28)+6), f'{name} {index}/11', fill='black')
    # Holds exist only in the review MP4; each lossless bridge is exactly 12 frames.
    for index in [0]*12+list(range(N))+[N-1]*18:
        rgb = np.concatenate([composite(frames[index]) for frames in sequences], axis=1)
        combined.write(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    combined.release()
    sheet.save(OUT/'contact.png')
    metrics['elapsed_seconds'] = round(time.monotonic()-started, 3)
    (OUT/'metrics.json').write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(elapsed=metrics['elapsed_seconds'], endpoints={name: [v['saved_first_equal'], v['saved_last_equal']]
                    for name, v in metrics['bridges'].items()}, output=str(OUT))))


if __name__ == '__main__':
    main()
