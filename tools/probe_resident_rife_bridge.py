"""Last bounded offline RIFE comparison: six raw RGB calls, no alpha averaging.

Outputs only runtime/resident-motion-20261108/bridge-probe/rife. A disposable
worker enforces a 100-second wall deadline including model setup and export;
timeouts kill that worker, never retry. MP4s are lossy review copies. The five
RGBA PNGs preserve first/last endpoints exactly. No network or source edits.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'runtime/resident-motion-20261108/bridge-probe/rife'
MODEL = ROOT/'runtime/build-tools/rife48-mirror.onnx'
TIMES = (0., .25, .5, .75, 1.)


def worker():
    import cv2
    import numpy as np
    import onnxruntime as ort
    from PIL import Image, ImageDraw

    started = time.monotonic()
    cv2.setNumThreads(2)
    background = np.array([160., 160., 160.], np.float32)
    report = dict(method='raw RIFE 4.8 BGR on constant gray, new foreground matte from RGB only',
                  alpha_interpolation=False, inference_limit=6, wall_budget_seconds=100,
                  intra_op_num_threads=2, inter_op_num_threads=1, status='running',
                  adopt=False, visual_review='pending', bridges={})

    def progress():
        report['elapsed_seconds'] = round(time.monotonic()-started, 3)
        (OUT/'metrics.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    def load(path):
        data = path.read_bytes()  # One snapshot; concurrent rebuilding is never retried.
        image = np.array(Image.open(io.BytesIO(data)).convert('RGBA'))
        return image, hashlib.sha256(data).hexdigest()

    def gray(rgba):
        alpha = rgba[..., 3:4].astype(np.float32)/255
        return rgba[..., :3].astype(np.float32)*alpha+background*(1-alpha)

    def matte(rgb):
        # All decisions come from this inferred RGB, never endpoint alpha.
        delta = np.max(np.abs(rgb.astype(np.float32)-background), axis=2)
        binary = (delta > 14).astype(np.uint8)*255
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            raise RuntimeError('No foreground found; no retry')
        mask = np.zeros(binary.shape, np.uint8)
        cv2.drawContours(mask, [max(contours, key=cv2.contourArea)], -1, 1, -1)
        inside = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
        outside = cv2.distanceTransform(1-mask, cv2.DIST_L2, 5)
        alpha = np.clip((inside-outside+1.0)/2., 0, 1)
        clean = np.clip((rgb-background*(1-alpha[..., None]))/np.maximum(alpha[..., None], .05), 0, 255)
        rgba = np.dstack((np.rint(clean).astype(np.uint8), np.rint(alpha*255).astype(np.uint8)))
        rgba[rgba[..., 3] == 0, :3] = 0
        return rgba, dict(foreground_area=int(mask.sum()), raw_components=len(contours),
                          selected_contour_area=float(max(map(cv2.contourArea, contours))))

    def checker(rgba):
        h, w = rgba.shape[:2]
        yy, xx = np.indices((h, w))
        bg = np.where(((xx//16+yy//16) % 2)[..., None], [203, 213, 224], [235, 240, 246])
        a = rgba[..., 3:4]/255.
        return np.rint(rgba[..., :3]*a+bg*(1-a)).astype(np.uint8)

    base, base_hash = load(OUT.parent/'base.png')
    h, w = base.shape[:2]
    if (w, h) != (320, 416) or w % 32 or h % 32:
        raise ValueError('Unexpected base geometry')
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 2
    opts.inter_op_num_threads = 1
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    opts.add_session_config_entry('session.intra_op.allow_spinning', '0')
    opts.add_session_config_entry('session.inter_op.allow_spinning', '0')
    session = ort.InferenceSession(str(MODEL), sess_options=opts, providers=['CPUExecutionProvider'])
    report['model'] = str(MODEL.relative_to(ROOT))
    report['model_sha256'] = hashlib.sha256(MODEL.read_bytes()).hexdigest()
    report['inputs'] = [dict(name=v.name, shape=v.shape, type=v.type) for v in session.get_inputs()]
    report['base_sha256'] = base_hash
    contact = Image.new('RGB', (w*5, (h+26)*2), 'white')
    raw_contact = contact.copy()
    draw, raw_draw = ImageDraw.Draw(contact), ImageDraw.Draw(raw_contact)
    count = 0
    for row, name in enumerate(('pout', 'doze')):
        source = ROOT/f'runtime/resident-motion-20261108/candidate/{name}/frames/0000.png'
        end, source_hash = load(source)
        if end.shape != base.shape:
            raise ValueError('Candidate size changed; no retry')
        folder = OUT/name
        folder.mkdir(exist_ok=True)
        start_rgb, end_rgb = gray(base), gray(end)
        # This local model expects BGR, unlike PIL's storage RGB.
        ia = (start_rgb[..., ::-1]/255.).transpose(2, 0, 1)[None].copy()
        ib = (end_rgb[..., ::-1]/255.).transpose(2, 0, 1)[None].copy()
        frames, raws, calls = [base.copy()], [start_rgb], []
        report['bridges'][name] = dict(source=str(source.relative_to(ROOT)), source_sha256=source_hash, calls=calls)
        progress()
        for t in TIMES[1:-1]:
            if count >= 6 or time.monotonic()-started >= 99:
                raise TimeoutError('Inference budget exhausted; no retry')
            before = time.monotonic()
            # Exactly one raw model call for each requested timestep.
            out = session.run(None, {'img0': ia, 'img1': ib, 'timestep': np.array([t], np.float32)})[0]
            duration = time.monotonic()-before
            count += 1
            rgb = np.clip(out[0].transpose(1, 2, 0)[..., ::-1]*255, 0, 255)
            if rgb.shape != start_rgb.shape or not np.isfinite(rgb).all():
                raise RuntimeError('Invalid RGB inference result; no retry')
            rgba, measurement = matte(rgb)
            frames.append(rgba)
            raws.append(rgb)
            calls.append(dict(t=t, seconds=round(duration, 3), **measurement))
            report['completed_inferences'] = count
            progress()
            print(json.dumps(dict(clip=name, t=t, seconds=round(duration, 3))), flush=True)
        frames.append(end.copy())
        raws.append(end_rgb)
        for i, (frame, raw) in enumerate(zip(frames, raws)):
            Image.fromarray(frame).save(folder/f'{i:04}.png')
            Image.fromarray(np.rint(raw).astype(np.uint8)).save(folder/f'raw-{i:04}.png')
            contact.paste(Image.fromarray(checker(frame)), (i*w, row*(h+26)+26))
            raw_contact.paste(Image.fromarray(np.rint(raw).astype(np.uint8)), (i*w, row*(h+26)+26))
            label = f'{name} t={TIMES[i]} / new matte'
            draw.text((i*w+5, row*(h+26)+6), label, fill='black')
            raw_draw.text((i*w+5, row*(h+26)+6), label.replace('new matte', 'raw RGB'), fill='black')
        first, _ = load(folder/'0000.png')
        last, _ = load(folder/'0004.png')
        report['bridges'][name].update(first_equal=bool(np.array_equal(first, base)),
                                       last_equal=bool(np.array_equal(last, end)),
                                       raw_midpoint_mae=[float(np.abs(raws[i]-raws[i-1]).mean()) for i in range(1, 5)])
        video = cv2.VideoWriter(str(OUT/f'{name}.mp4'), cv2.VideoWriter_fourcc(*'mp4v'), 12, (w, h))
        if not video.isOpened():
            raise RuntimeError('MP4 encoder unavailable')
        try:
            # Only five inferred/sample moments; repeat for inspection, no extra interpolation.
            for index in [0]*8+[1]*4+[2]*4+[3]*4+[4]*8:
                video.write(cv2.cvtColor(checker(frames[index]), cv2.COLOR_RGB2BGR))
        finally:
            video.release()
    contact.save(OUT/'contact.png')
    raw_contact.save(OUT/'raw-contact.png')
    report.update(status='completed', completed_inferences=count)
    progress()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if '--worker' in sys.argv:
        worker()
        return
    started = time.monotonic()
    command = [sys.executable, '-B', str(Path(__file__).resolve()), '--worker']
    try:
        result = subprocess.run(command, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding='utf-8', timeout=100,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        print(result.stdout, end='')
        if result.returncode:
            raise RuntimeError(result.stderr[-1500:] or 'Worker failed')
        print(json.dumps(dict(status='completed', wall_seconds=round(time.monotonic()-started, 3), output=str(OUT))))
    except (subprocess.TimeoutExpired, RuntimeError) as exc:
        path = OUT/'metrics.json'
        report = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        report.update(status='stopped_without_retry', adopt=False, error=str(exc), wall_seconds=round(time.monotonic()-started, 3))
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        raise SystemExit('Probe stopped, no retry: '+str(exc))


if __name__ == '__main__':
    main()
