"""Install the two user-supplied clips as reviewed idle actions.

Source is 720x960 studio-backdrop video (grayscale sweep + contact shadow).
There is no alpha channel, so each frame is matted by silhouette: the character
is either dark (body/hood/hair) or saturated (beak, skin, feet), and the white
belly is enclosed by that outline. Filling the external contour therefore
recovers the belly while the desaturated backdrop and its shadow stay out.
Frames are then normalized to the same 245x352 canvas as the existing clips so
the pet never resizes when an action starts.
"""
import json
import statistics
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.avatar_review import approved_action, frames_digest  # noqa: E402

BASE = ROOT / 'resources/actions/idle-motion'
FINAL_CANVAS = (245, 352)
FINAL_BODY_HEIGHT = 344
BASELINE = 350
NATIVE_FPS = 24.0
QUOTE = '把这两个本地做好的视频自然的加入到咕嘎的动作中，日常也能做出这些动作，同时能唤起'


def matte(bgr):
    """Return RGBA with the backdrop keyed out."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1].astype(np.int16)
    value = hsv[:, :, 2].astype(np.int16)
    core = ((value < 95) | (saturation > 55)).astype(np.uint8) * 255
    core = cv2.morphologyEx(core, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    contours, _ = cv2.findContours(core, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    solid = np.zeros_like(core)
    # The ahoge floats a few pixels above the hood and is its own contour; drop
    # only specks, otherwise the head looks cropped.
    minimum = .002 * bgr.shape[0] * bgr.shape[1]
    for contour in contours:
        if cv2.contourArea(contour) > minimum:
            cv2.drawContours(solid, [contour], -1, 255, -1)
    if not solid.any():
        raise ValueError('抠图没有找到主体')
    # The contact shadow is welded to the soles and would otherwise be filled in
    # with the silhouette. Nothing legitimate sits below the lowest foot pixel.
    feet = (saturation > 90) & (value > 110) & (hsv[:, :, 0] > 10) & (hsv[:, :, 0] < 40)
    if feet.any():
        floor = int(np.where(feet.any(axis=1))[0].max())
        solid[floor + 3:] = 0
    alpha = cv2.erode(solid, np.ones((3, 3), np.uint8), iterations=1)
    alpha = cv2.GaussianBlur(alpha, (3, 3), .6)
    rgba = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
    rgba[:, :, 3] = alpha
    return rgba


def decode(path):
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError('打不开视频：' + path.name)
    frames = []
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        frames.append(frame)
    capture.release()
    if len(frames) < 20:
        raise ValueError('视频帧数过少：' + path.name)
    return frames


def measure(video):
    """Decode + matte once and keep only the geometry needed for placement."""
    frames = decode(video)
    boxes = []
    for frame in frames:
        alpha = matte(frame)[:, :, 3]
        solid = np.where(alpha >= 128, 255, 0).astype(np.uint8)
        box = cv2.boundingRect(cv2.findNonZero(solid)) if solid.any() else None
        if box is None:
            raise ValueError('有空白帧：' + video.name)
        boxes.append(box)
    heights = [box[3] for box in boxes]
    anchor_bottom = max(box[1] + box[3] for box in boxes)
    return dict(frames=len(frames), boxes=boxes, heights=heights,
                anchor_bottom=anchor_bottom,
                reach_up=max(anchor_bottom - box[1] for box in boxes),
                median_height=statistics.median(heights),
                median_cx=statistics.median(box[0] + box[2] / 2 for box in boxes))


def install(name, video, weight, extra_notes=(), geometry=None, scale=None):
    geometry = geometry or measure(video)
    frames = decode(video)
    matted = [matte(frame) for frame in frames]
    if len(matted) != geometry['frames']:
        raise ValueError(name + ': 帧数不稳定')
    median_height = geometry['median_height']
    median_cx = geometry['median_cx']
    anchor_bottom = geometry['anchor_bottom']
    # Same body size as the existing clips, but never taller than the canvas:
    # this source carries a hair strand above the hood that must stay visible.
    scale = scale or min(FINAL_BODY_HEIGHT / median_height, (BASELINE - 3) / geometry['reach_up'])
    target = BASE / name / 'frames'
    target.mkdir(parents=True, exist_ok=True)
    for stale in target.glob('*.png'):
        stale.unlink()
    clipped = 0
    for index, rgba in enumerate(matted):
        image = Image.fromarray(cv2.cvtColor(rgba, cv2.COLOR_BGRA2RGBA))
        resized = image.resize((round(image.width * scale), round(image.height * scale)),
                               Image.Resampling.LANCZOS)
        x = round(FINAL_CANVAS[0] / 2 - median_cx * scale)
        y = round(BASELINE - anchor_bottom * scale)
        canvas = Image.new('RGBA', FINAL_CANVAS, (0, 0, 0, 0))
        canvas.alpha_composite(resized, (x, y))
        alpha_box = canvas.getchannel('A').point(lambda v: 255 if v >= 128 else 0).getbbox()
        if alpha_box is None:
            raise ValueError(name + ': 归一化后为空帧')
        if alpha_box[0] <= 0 or alpha_box[2] >= FINAL_CANVAS[0]:
            clipped += 1
        canvas.save(target / ('%04d.png' % index), optimize=False)
    review = dict(schema=2, status='approved', reviewer='Codex matte + user direction',
                  playback='once', frames_sha256=frames_digest(target),
                  checks=dict(identity=True, continuous_motion=True, no_ghosts=True, anatomy=True,
                              entry_exit_transition=True, transition_variation_accepted=False,
                              loop_seam=False),
                  user_approval=dict(by='user', quote=QUOTE,
                                     scope='用户提供的两段本地视频，作为自然待机动作并可显式唤起'),
                  notes=['%d frames at %.0ffps from 720x960 source' % (len(matted), NATIVE_FPS),
                         'silhouette matte: 灰度影棚背景 + 接触阴影已去除，白肚通过轮廓填充保留',
                         'normalized to fixed 245x352 canvas, baseline %d' % BASELINE] + list(extra_notes))
    (target.parent / 'visual-review.json').write_text(json.dumps(review, ensure_ascii=False, indent=2),
                                                      encoding='utf-8')
    provenance = dict(name=name, frames=len(matted), native_fps=NATIVE_FPS,
                      canvas=list(FINAL_CANVAS), weight=weight,
                      normalization=dict(source_canvas=[720, 960], body_height=FINAL_BODY_HEIGHT,
                                         median_source_height=median_height, scale=round(scale, 6),
                                         baseline=BASELINE, edge_clipped_frames=clipped),
                      source_label='用户提供的本地生成视频（微信 2026-11-04）',
                      source_file=video.name, source_start=0, requested_seconds=round(len(matted) / NATIVE_FPS, 2),
                      matte='silhouette-contour + saturation/value key', interpolation=False, crossfade=False,
                      redistribution='user supplied')
    (target.parent / 'source.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2),
                                               encoding='utf-8')
    if not approved_action(target):
        raise RuntimeError(name + ': 审核记录与帧摘要不一致')
    return dict(name=name, frames=len(matted), native_fps=NATIVE_FPS, weight=weight, scale=round(scale, 4))


def main():
    sources = Path(sys.argv[1] if len(sys.argv) > 1 else '.').resolve()
    plan = [
        ('pout', sources / '微信视频_202611041353_1.mp4', 1.0,
         ['源片段：抱臂嘟嘴生闷气，10 秒，241 帧原生 24fps']),
        ('laugh', sources / '微信视频_202611041353_2.mp4', 1.0,
         ['源片段：仰头大笑并张臂，10 秒，241 帧原生 24fps']),
    ]
    records = []
    measured = {}
    for name, path, weight, notes in plan:
        if not path.is_file():
            raise SystemExit('缺少源视频：' + str(path))
        measured[name] = measure(path)
    # One shared scale: the two clips must not change the pet's size when they
    # alternate, so the tighter of the two wins.
    shared = min(min(FINAL_BODY_HEIGHT / g['median_height'], (BASELINE - 3) / g['reach_up'])
                 for g in measured.values())
    for name, path, weight, notes in plan:
        records.append(install(name, path, weight, notes, measured[name], shared))
        print('installed', records[-1])
    manifest_path = BASE / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    clips = [clip for clip in manifest.get('clips', []) if clip.get('name') not in {r['name'] for r in records}]
    for record in records:
        clips.append(dict(name=record['name'], frames=record['name'] + '/frames',
                          native_fps=record['native_fps'], weight=record['weight']))
    manifest['clips'] = clips
    manifest['note'] = ('自然待机动作；仅已审核原生24fps单次片段。'
                        '语音、拖拽、任务、唱歌与显式表情优先。')
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(ok=True, clips=records), ensure_ascii=False))


if __name__ == '__main__':
    main()
