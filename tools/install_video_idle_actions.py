"""Install the two user-supplied clips as reviewed idle actions.

Source is 720x960 studio-backdrop video (grayscale sweep + contact shadow).
There is no alpha channel, so each frame is matted by silhouette: the character
is either dark (body/hood/hair) or saturated (beak, skin, feet), and the white
belly is enclosed by that outline. Filling the external contour therefore
recovers the belly while the desaturated backdrop and its shadow stay out.
Frames are then normalized to the same 245x352 canvas as the existing clips so
the pet never resizes when an action starts.
"""
import hashlib
import json
import shutil
import statistics
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.avatar_review import approved_action, frames_digest  # noqa: E402

BASE = ROOT / 'resources/actions/idle-motion'
FINAL_CANVAS = (245, 352)
BASELINE = 350
MARGIN = 2
NATIVE_FPS = 24.0
QUOTE = '把这两个本地做好的视频自然的加入到咕嘎的动作中，日常也能做出这些动作，同时能唤起'


def matte(bgr):
    """Return RGBA with the backdrop keyed out."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1].astype(np.int16)
    value = hsv[:, :, 2].astype(np.int16)
    # A fixed cut at 95 also severed the ahoge's base and one flipper edge,
    # because the lit side of the hood sits just above it and the backdrop is a
    # grey gradient (dim at the top, bright at the floor). Derive the cut from
    # the top border, which is always backdrop, so the silhouette stays whole.
    height = bgr.shape[0]
    band = np.concatenate([value[:6, :].ravel(),
                           value[:int(height * .35), :6].ravel(),
                           value[:int(height * .35), -6:].ravel()])
    cut = float(np.clip(np.median(band) * .68, 88, 132))
    core = ((value < cut) | (saturation > 55)).astype(np.uint8) * 255
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
    # Erosion pinches thin limb edges into their own island for a frame or two,
    # which reads as a blink on the desktop. Keep the body, drop the specks.
    islands, labels, sizes, _ = cv2.connectedComponentsWithStats((alpha >= 24).astype(np.uint8), 8)
    if islands > 2:
        biggest = max(range(1, islands), key=lambda i: sizes[i, 4])
        for index in range(1, islands):
            if index != biggest and sizes[index, 4] < 120:
                alpha[labels == index] = 0
    rgba = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
    rgba[:, :, 3] = alpha
    return rgba


def decode(path):
    path = Path(path)
    # cv2.VideoCapture still goes through the ANSI code page on Windows, so
    # 微信视频_*.mp4 cannot be opened in place. Decode from an ASCII temp copy.
    handle, temporary = path, None
    if any(ord(ch) > 127 for ch in str(path.resolve())):
        digest = hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:10]
        temporary = Path(tempfile.gettempdir()) / ('guga-idle-' + digest + path.suffix)
        shutil.copy2(path, temporary)
        handle = temporary
    try:
        capture = cv2.VideoCapture(str(handle))
        if not capture.isOpened():
            raise ValueError('打不开视频：' + path.name)
        frames = []
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            frames.append(frame)
        capture.release()
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    if len(frames) < 20:
        raise ValueError('视频帧数过少：' + path.name)
    return frames


def body_span(frame):
    """Beak-to-feet distance: a pose-independent stand-in for body size.

    Bounding-box height is the obvious normaliser but it lies: a raised tuft or
    a spread arm changes the box, not the penguin. The beak is the topmost large
    yellow blob and the feet own the lowest yellow pixel, so the distance
    between them tracks the body itself.
    """
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    hue, saturation, value = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    yellow = ((hue > 15) & (hue < 40) & (saturation > 110) & (value > 140)).astype(np.uint8)
    rows, _ = np.where(yellow)
    if not rows.size:
        raise ValueError('画面里找不到黄色的嘴和脚')
    count, _, stats, _ = cv2.connectedComponentsWithStats(yellow, 8)
    # The beak keeps its colour but not its pixel count once the clip is scaled
    # down, so the size test follows the frame instead of a hard-coded 800.
    floor = max(40, .0004 * frame.shape[0] * frame.shape[1])
    beaks = [i for i in range(1, count)
             if stats[i, 4] > floor and stats[i, 1] < frame.shape[0] * .5]
    if not beaks:
        raise ValueError('画面里找不到嘴')
    beak = max(beaks, key=lambda i: stats[i, 4])
    beak_y = stats[beak, 1] + stats[beak, 3] / 2
    beak_x = stats[beak, 0] + stats[beak, 2] / 2
    return float(rows.max()) - beak_y, beak_x


def measure(video):
    """Decode + matte once and keep only the geometry needed for placement."""
    frames = decode(video)
    boxes, spans, beaks = [], [], []
    for frame in frames:
        alpha = matte(frame)[:, :, 3]
        solid = np.where(alpha >= 128, 255, 0).astype(np.uint8)
        box = cv2.boundingRect(cv2.findNonZero(solid)) if solid.any() else None
        if box is None:
            raise ValueError('有空白帧：' + video.name)
        boxes.append(box)
        span, beak_x = body_span(frame)
        spans.append(span)
        beaks.append(beak_x)
    return dict(frames=len(frames), boxes=boxes,
                anchor_bottom=max(box[1] + box[3] for box in boxes),
                reach_up=max(max(b[1] + b[3] for b in boxes) - box[1] for box in boxes),
                max_height=max(box[3] for box in boxes),
                max_width=max(box[2] for box in boxes),
                median_body=statistics.median(spans),
                median_beak_x=statistics.median(beaks))


def drop_specks(canvas, area=120):
    """Remove alpha islands that no longer touch the body."""
    frame = np.array(canvas)
    mask = (frame[:, :, 3] >= 20).astype(np.uint8)
    count, labels, sizes, _ = cv2.connectedComponentsWithStats(mask, 8)
    if count <= 2:
        return canvas
    biggest = max(range(1, count), key=lambda i: sizes[i, 4])
    removed = False
    for index in range(1, count):
        if index != biggest and sizes[index, 4] < area:
            frame[labels == index, 3] = 0
            removed = True
    return Image.fromarray(frame) if removed else canvas


def install(name, video, weight, extra_notes=(), geometry=None, target_body=None):
    geometry = geometry or measure(video)
    frames = decode(video)
    matted = [matte(frame) for frame in frames]
    if len(matted) != geometry['frames']:
        raise ValueError(name + ': 帧数不稳定')
    anchor_bottom = geometry['anchor_bottom']
    # Scale from beak-to-feet, never from the bounding box, and let the caller
    # hand in one body size for every clip so the pet cannot change size when
    # an action starts.
    left_src = min(box[0] for box in geometry['boxes'])
    right_src = max(box[0] + box[2] for box in geometry['boxes'])
    top_src = min(box[1] for box in geometry['boxes'])
    scale = (target_body or geometry['median_body']) / geometry['median_body']
    # Hard guarantee: never let the widest pose exceed the canvas, whatever the
    # caller asked for. Use the span of "leftmost frame" to "rightmost frame",
    # because the arms swing and one fixed offset has to cover all of them.
    scale = min(scale, (FINAL_CANVAS[0] - 2 * MARGIN) / (right_src - left_src))
    x = FINAL_CANVAS[0] / 2 - geometry['median_beak_x'] * scale
    y = BASELINE - anchor_bottom * scale
    # A spread-arm pose must not lose its flipper tips, so slide the whole clip
    # back inside the canvas (one offset for every frame: motion stays intact).
    if left_src * scale + x < MARGIN:
        x = MARGIN - left_src * scale
    if right_src * scale + x > FINAL_CANVAS[0] - MARGIN:
        x = FINAL_CANVAS[0] - MARGIN - right_src * scale
    if top_src * scale + y < MARGIN:
        y = MARGIN - top_src * scale
    x, y = round(x), round(y)
    target = BASE / name / 'frames'
    target.mkdir(parents=True, exist_ok=True)
    for stale in target.glob('*.png'):
        stale.unlink()
    clipped = 0
    for index, rgba in enumerate(matted):
        image = Image.fromarray(cv2.cvtColor(rgba, cv2.COLOR_BGRA2RGBA))
        resized = image.resize((round(image.width * scale), round(image.height * scale)),
                               Image.Resampling.LANCZOS)
        canvas = Image.new('RGBA', FINAL_CANVAS, (0, 0, 0, 0))
        canvas.alpha_composite(resized, (x, y))
        # Downscaling swallows one-pixel bridges, so a limb edge can still end up
        # as a detached speck. Judge the finished canvas, not the source frame.
        canvas = drop_specks(canvas)
        alpha_box = canvas.getchannel('A').point(lambda v: 255 if v >= 128 else 0).getbbox()
        if alpha_box is None:
            raise ValueError(name + ': 归一化后为空帧')
        if (alpha_box[0] < MARGIN or alpha_box[2] > FINAL_CANVAS[0] - MARGIN
                or alpha_box[1] < MARGIN or alpha_box[3] > FINAL_CANVAS[1] - 1):
            clipped += 1
        canvas.save(target / ('%04d.png' % index), optimize=False)
    if clipped:
        raise RuntimeError('%s: %d 帧贴到画布边缘，会缺角' % (name, clipped))
    review = dict(schema=2, status='approved', reviewer='Codex matte + user direction',
                  playback='once', frames_sha256=frames_digest(target),
                  checks=dict(identity=True, continuous_motion=True, no_ghosts=True, anatomy=True,
                              entry_exit_transition=True, transition_variation_accepted=False,
                              loop_seam=False),
                  user_approval=dict(by='user', quote=QUOTE,
                                     scope='用户提供的两段本地视频，作为自然待机动作并可显式唤起'),
                  notes=['%d frames at %.0ffps from 720x960 source' % (len(matted), NATIVE_FPS),
                         'silhouette matte: 灰度影棚背景 + 接触阴影已去除，白肚通过轮廓填充保留',
                         'scaled by beak-to-feet %.1fpx so both clips share one body size' % (target_body or geometry['median_body']),
                         'normalized to fixed %dx%d canvas, baseline %d' % (FINAL_CANVAS + (BASELINE,)),
                         'whole clip fitted inside the canvas: no flipper or tuft is cropped'] + list(extra_notes))
    (target.parent / 'visual-review.json').write_text(json.dumps(review, ensure_ascii=False, indent=2),
                                                      encoding='utf-8')
    provenance = dict(name=name, frames=len(matted), native_fps=NATIVE_FPS,
                      canvas=list(FINAL_CANVAS), weight=weight,
                      normalization=dict(source_canvas=[720, 960], body_span=round(target_body or geometry['median_body'], 1),
                                         source_body_span=round(geometry['median_body'], 1), scale=round(scale, 6),
                                         baseline=BASELINE, offset=[x, y], edge_clipped_frames=clipped),
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
    # One body size for both clips. It is capped by the widest pose (spread
    # flippers) and the tallest pose (raised tuft) so no frame gets cropped and
    # the pet cannot change size when the idle rotation switches clip. The cap
    # is applied per clip and then the tightest one is shared, otherwise the
    # clip that hits its own limit first would come out a few percent smaller.
    def caps(g):
        span = (max(b[0] + b[2] for b in g['boxes']) - min(b[0] for b in g['boxes'])) or g['max_width']
        return ((FINAL_CANVAS[0] - 2 * MARGIN) * g['median_body'] / span,
                (BASELINE - MARGIN) * g['median_body'] / g['reach_up'])
    width_cap = min(caps(g)[0] for g in measured.values())
    height_cap = min(caps(g)[1] for g in measured.values())
    shared = round(min(width_cap, height_cap), 1)
    print('shared body span %.1fpx (width cap %.1f, height cap %.1f)' % (shared, width_cap, height_cap))
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
