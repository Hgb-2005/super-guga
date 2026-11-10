"""Repair two bounded local source clips; only alpha changes, never RGB/time.

Run with python -B. Outputs remain outside production assets and approvals.
source.mp4 is an exact copy of the original (MP4 does not carry this alpha).
"""
import hashlib
import json
import runpy
import shutil
import time
from fractions import Fraction
from pathlib import Path

import av
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'runtime/resident-motion-20261108/repaired-source'
INPUTS = [Path('G:/下载文件/豆包画图 (2).mp4'), Path('G:/下载文件/豆包画图 (4).mp4')]
LIMIT = 300


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    started = time.monotonic()
    probe = runpy.run_path(str(ROOT/'tools/probe_resident_matte.py'))
    old_matte, row_matte, preview = probe['original_matte'](), probe['row_matte'], probe['preview']
    refs = {key: np.array(Image.open(ROOT/'runtime/video-intake'/f'{ref}_first.png').convert('RGB'))
            for key, ref in [('pout', 'v1'), ('laugh', 'v2')]}
    plans = []
    # Confirm both identities before creating output files.
    for source in INPUTS:
        with av.open(str(source)) as container:
            stream = container.streams.video[0]
            first = next(container.decode(stream)).to_ndarray(format='rgb24')
            errors = {name: float(np.abs(first.astype(np.float32)-ref).mean())
                      if first.shape == ref.shape else 1e9 for name, ref in refs.items()}
            name = min(errors, key=errors.get)
            if errors[name] > .1 or stream.frames > LIMIT:
                raise ValueError(f'Identity/frame limit failed: {source.name}: {errors}')
            plans.append((name, source, errors))
    if len({p[0] for p in plans}) != 2:
        raise ValueError('Sources do not uniquely match pout and laugh')
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 19)
    summaries = []
    for name, source, errors in plans:
        folder = OUT/name
        frames_dir = folder/'frames'
        if frames_dir.exists() and any(frames_dir.iterdir()):
            raise FileExistsError(f'Refusing to overwrite existing frames: {frames_dir}')
        frames_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, folder/'source.mp4')
        source_hash = sha256(source)
        assert source_hash == sha256(folder/'source.mp4')
        anomalies, measures, timestamps, samples = [], [], [], {}
        with av.open(str(source)) as container:
            stream = container.streams.video[0]
            fps = stream.average_rate or Fraction(24, 1)
            declared = stream.frames
            if not declared or declared > LIMIT:
                raise ValueError('Source requires a known bounded frame count')
            sample_indices = set(np.linspace(0, declared-1, 9).round().astype(int).tolist())
            for index, frame in enumerate(container.decode(stream)):
                if index >= LIMIT or time.monotonic()-started > 145:
                    raise TimeoutError('Frame/time budget reached')
                rgb = frame.to_ndarray(format='rgb24')
                bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
                old = old_matte(bgr)[:, :, 3]
                row18, floor = row_matte(bgr, 18)
                h, w = old.shape
                boundary = round(.58*h)
                alpha = old.copy()
                alpha[:boundary-1] = row18[:boundary-1]
                for y, weight in zip(range(boundary-1, boundary+2), (1., .5, 0.)):
                    alpha[y] = np.rint(weight*row18[y].astype(float)+(1-weight)*old[y]).astype(np.uint8)
                assert np.array_equal(alpha[boundary+2:], old[boundary+2:])
                rgba = np.dstack([rgb, alpha])
                assert np.array_equal(rgba[:, :, :3], rgb)
                target = frames_dir/f'{index:04d}.png'
                Image.fromarray(rgba).save(target, compress_level=1)
                solid = alpha > 128
                yy, xx = np.where(solid)
                flags = []
                if not len(xx):
                    flags.append('empty_alpha')
                elif xx.min() < 2 or xx.max() >= w-2 or yy.min() < 2:
                    flags.append('touches_source_edge')
                rh = np.s_[int(h*.05):int(h*.56), int(w*.55):int(w*.94)]
                tuft = np.s_[:int(h*.16), int(w*.30):int(w*.70)]
                floor_added = int(((alpha > old) & (np.indices(old.shape)[0] >= boundary+2)).sum())
                floor_residual = int((old[floor+2:] > 20).sum())
                recovered = int(((alpha > 128) & (old < 128))[rh].sum())
                tuft_pixels = int(solid[tuft].sum())
                if recovered < 200:
                    flags.append('low_right_head_recovery')
                if name == 'pout' and tuft_pixels < 30:
                    flags.append('tuft_roi_empty')
                if floor_residual:
                    flags.append('inherited_old_floor_alpha')
                if floor_added:
                    flags.append('new_floor_alpha')
                count, _, stats, _ = cv2.connectedComponentsWithStats(solid.astype(np.uint8), 8)
                islands = int(sum(area >= 20 for area in stats[1:, 4]))
                if islands > 3:
                    flags.append('multiple_alpha_islands')
                timestamps.append(float(frame.pts*frame.time_base) if frame.pts is not None else None)
                measure = dict(frame=index, recovered_right_head=recovered, tuft_roi_pixels=tuft_pixels,
                               inherited_floor_pixels=floor_residual, new_floor_pixels=floor_added,
                               alpha_area=int(solid.sum()), significant_components=islands, flags=flags)
                measures.append(measure)
                if flags:
                    anomalies.append(measure)
                if index in sample_indices:
                    # Re-open saved PNG: verify deliverable, not just in-memory arrays.
                    saved = np.array(Image.open(target))
                    assert np.array_equal(saved, rgba)
                    samples[index] = (rgb, old, saved)
        count = len(measures)
        if count != declared:
            raise ValueError(f'Decoded {count}, declared {declared}')
        for i in range(1, count):
            ratio = abs(measures[i]['alpha_area']-measures[i-1]['alpha_area'])/max(1, measures[i-1]['alpha_area'])
            if ratio > .12:
                anomalies.append({'frame': i, 'flags': ['alpha_area_jump'], 'relative_change': ratio})
        full = Image.new('RGB', (720, 80+3*350), 'white')
        detail = Image.new('RGB', (960, 80+9*240), 'white')
        fd, dd = ImageDraw.Draw(full), ImageDraw.Draw(detail)
        title = f'{name}｜9帧抽检｜全视频已导出，时序效果待验收'
        fd.text((10, 8), title, font=font, fill='black')
        dd.text((10, 8), title, font=font, fill='black')
        for column, label in enumerate(('旧右头', '修复右头', '修复顶部/呆毛', '脚底（旧alpha）')):
            dd.text((column*240+4, 45), label, font=font, fill='black')
        for n, (index, (rgb, old, rgba)) in enumerate(sorted(samples.items())):
            h, w = old.shape
            old_preview = preview(np.dstack([rgb, old]))
            new_preview = preview(rgba)
            x, y = (n%3)*240, 70+(n//3)*350
            fd.text((x+5, y), f'帧 {index}', font=font, fill='black')
            full.paste(new_preview.resize((240, 320)), (x, y+26))
            regions = [(old_preview, (.55, .05, .94, .56)),
                       (new_preview, (.55, .05, .94, .56)),
                       (new_preview, (.25, 0, .75, .20)),
                       (new_preview, (.10, .83, .90, 1.))]
            for c, (im, box) in enumerate(regions):
                crop = im.crop(tuple(round(v*(w if j%2 == 0 else h)) for j, v in enumerate(box)))
                panel = ImageOps.contain(crop, (238, 210))
                detail.paste(panel, (c*240, 80+n*240+25))
                dd.text((c*240+4, 80+n*240), f'帧 {index}', font=font, fill='black')
        full.save(folder/'contact_full_9.png')
        detail.save(folder/'contact_righthead_tuft_floor_9.png')
        summary = dict(name=name, original_source=str(source), copied_source='source.mp4',
                       source_mp4_is_original_not_alpha_video=True, sha256=source_hash,
                       reference_first_frame_mae=errors, frame_count=count, declared_frames=declared,
                       fps=float(fps), fps_fraction=str(fps), size=[w, h],
                       duration_frames_seconds=count/float(fps), timestamps_seconds=timestamps,
                       method='head row18 / lower old alpha / three-row alpha-only handoff at .58h',
                       rgb_unchanged=True, temporal_blending=False, generated_pixels=False,
                       mirrored_pixels=False, sample_indices=sorted(samples),
                       sample_checks=[measures[i] for i in sorted(samples)],
                       anomalous_frames=anomalies,
                       limitations='Lower alpha deliberately retains old matte defects; nine visual samples only; automated flags require review.')
        save_json(folder/'source.json', summary)
        summaries.append({k: summary[k] for k in ('name', 'original_source', 'frame_count', 'fps', 'sample_indices')}
                             | {'anomaly_records': len(anomalies)})
        print(json.dumps(summaries[-1], ensure_ascii=True), flush=True)
    save_json(OUT/'summary.json', dict(clips=summaries, elapsed_seconds=round(time.monotonic()-started, 2)))


if __name__ == '__main__':
    main()
