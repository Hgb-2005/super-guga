"""Finite, offline review of the *real* resident/idle-motion transitions.

Run from any directory: python tools/preview_resident_motion.py --label before
Repeat with --label after following renderer changes. No window/event loop,
network, application settings or live pet process is involved. Only this tool's
output directory is written. Requires the project's PyQt5, numpy and PyAV.

Outputs runtime/resident-motion-20261108/<label>.mp4, contact.png, metrics.json.
The latter two describe the latest run; <label>/contact.png and metrics.json
also preserve each run for before/after comparisons. Contact rows are moments,
columns are doze/heart/pout/laugh, sampled from the exact encoded frames.
"""

import argparse
import json
import math
import os
import sys
from contextlib import ExitStack
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "runtime" / "resident-motion-20261108"
NAMES = ("doze", "heart", "pout", "laugh")
FPS = 30
WIDTH, HEIGHT, COLUMN = 1440, 500, 360
ALPHA_CUTOFF = 64  # Renderer shadow peaks at 40; exclude its faint footprint.
MOMENTS = (
    "resident", "entry_first", "entry_0.15s", "motion_middle",
    "motion_last", "return_first", "return_0.15s", "restored",
)


class FrameClock:
    """All four instances see exactly the same monotonic timestamp per frame."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def rgba_pixels(image, np):
    ptr = image.constBits()
    ptr.setsize(image.sizeInBytes())
    # Respect QImage's row stride rather than assuming an unpadded buffer.
    rows = np.frombuffer(ptr, dtype=np.uint8).reshape(image.height(), image.bytesPerLine())
    return rows[:, :image.width() * 4].reshape(image.height(), image.width(), 4).copy()


def measure(pixels, previous, baseline, np):
    """Measure the rendered silhouette, never source frames or text/background."""
    solid = pixels[:, :, 3] >= ALPHA_CUTOFF
    ys, xs = np.nonzero(solid)
    if not len(xs):
        raise RuntimeError("Real draw() produced no character pixels above alpha cutoff")
    left, top, right, bottom = map(int, (xs.min(), ys.min(), xs.max(), ys.max()))
    result = dict(bbox_xyxy=[left, top, right, bottom], foot_y=bottom,
                  width=right - left + 1, height=bottom - top + 1,
                  solid_pixels=int(solid.sum()),
                  touches_canvas_edge=bool(left == 0 or top == 0 or
                                           right == COLUMN - 1 or bottom == HEIGHT - 1))
    if baseline is not None:
        result["vs_resident"] = {
            key: result[key] - baseline[key] for key in ("foot_y", "width", "height")
        }
    if previous is not None:
        old_solid = previous[:, :, 3] >= ALPHA_CUTOFF
        union = solid | old_solid
        # Compare premultiplied RGBA after suppressing low-alpha shadow pixels.
        # Transparent RGB garbage therefore cannot inflate the difference.
        current = pixels.astype(np.float32)
        old = previous.astype(np.float32)
        current[:, :, :3] *= current[:, :, 3:4] / 255.0
        old[:, :, :3] *= old[:, :, 3:4] / 255.0
        current[~solid] = 0
        old[~old_solid] = 0
        delta = np.abs(current - old)
        result["adjacent_difference"] = dict(
            premultiplied_rgba_mae=float(delta[union].mean()),
            changed_pixel_fraction=float((delta.max(axis=2)[union] > 2).mean()),
            silhouette_iou=float((solid & old_solid).sum() / max(1, union.sum())),
            foot_y_delta=bottom - int(np.nonzero(old_solid)[0].max()),
        )
    return result


def summarize(records, return_frame, np):
    result = {}
    for key in ("foot_y", "width", "height"):
        values = [row[key] for row in records]
        result[key] = dict(min=min(values), max=max(values), span=max(values) - min(values),
                           max_abs_vs_resident=max(abs(v - records[FPS - 1][key]) for v in values))
    result["canvas_edge_frames"] = [r["frame"] for r in records if r["touches_canvas_edge"]]
    differences = [r["adjacent_difference"]["premultiplied_rgba_mae"] for r in records[1:]]
    result["adjacent_mae"] = dict(max=max(differences), p95=float(np.percentile(differences, 95)))
    # Include the whole entry/exit neighbourhood, not just the maximum over a clip.
    result["switch_neighbours"] = {
        name: [r for r in records if abs(r["frame"] - anchor) <= 5]
        for name, anchor in (("entry", FPS), ("return", return_frame))
    }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, choices=("before", "after"))
    parser.add_argument('--candidate',action='store_true',help='isolated review of uninstalled local candidates')
    args = parser.parse_args(argv)

    # Force offscreen even if the caller normally runs Qt with a desktop plugin.
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, str(ROOT))
    from launcher import _prepare_qt
    _prepare_qt()
    import av
    import numpy as np
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QColor, QFont, QImage, QPainter
    from PyQt5.QtWidgets import QApplication
    import ui.sprite_animator as animator_module

    app = QApplication.instance() or QApplication([])
    clock = FrameClock()
    pets = []
    states = []
    OUTPUT.mkdir(parents=True, exist_ok=True)
    archive = OUTPUT / args.label
    archive.mkdir(parents=True, exist_ok=True)
    video_path = OUTPUT / (args.label + ".mp4")
    background = QColor("#edf0f3")

    def annotate(panel, name, phase, timestamp, foot_line):
        painter = QPainter(panel)
        try:
            painter.setPen(QColor("#b3bbc5"))
            painter.drawLine(12, foot_line, COLUMN - 12, foot_line)
            painter.setPen(QColor("#263445"))
            painter.setFont(QFont("Arial", 12))
            painter.drawText(12, 25, name + " | " + args.label)
            painter.setFont(QFont("Arial", 9))
            painter.drawText(12, 46, "%.3fs | %s" % (timestamp, phase))
            painter.drawText(12, HEIGHT - 12, "30 fps review | native clip timing")
        finally:
            painter.end()

    # Preserve the real play_clip/stop_clip/_tick/draw methods. Patching only the
    # clock/random source avoids random sleep, while infinite countdowns disable
    # autonomous expressions, gestures, blink and wing twitch. Breathing stays real.
    with patch.object(animator_module.time, "monotonic", clock), \
            patch.object(animator_module.random, "random", return_value=1.0), ExitStack() as review_scope:
        if args.candidate:
            candidate=OUTPUT/'candidate'
            original_loader=animator_module.SpriteAnimator._load_idle_clips
            review_scope.enter_context(patch.object(animator_module.SpriteAnimator,'_load_idle_clips',
                lambda self:original_loader(self,candidate)))
            # Test-only, path-confined loading. The application approval gate
            # stays intact and the candidate remains outside live resources.
            review_scope.enter_context(patch('core.avatar_review.approved_action',
                side_effect=lambda folder:Path(folder).resolve().is_relative_to(candidate.resolve())))
        try:
            for name in NAMES:
                pet = animator_module.SpriteAnimator(str(ROOT / "resources"), 260)
                pets.append(pet)
                pet.stop()
                pet.set_expression("normal")
                for field in ("_idle_action_countdown", "_idle_expr_countdown",
                              "_blink_countdown", "_wing_twitch_countdown"):
                    setattr(pet, field, math.inf)
                spec = pet.clips.get("idle:" + name)
                if not spec or not spec["frames"] or not math.isfinite(float(spec["fps"])) or spec["fps"] <= 0:
                    raise RuntimeError("Missing or invalid installed idle clip: " + name)
                duration = len(spec["frames"]) / float(spec["fps"])
                states.append(dict(name=name, native_fps=float(spec["fps"]),
                                   native_frames=len(spec["frames"]), native_seconds=duration,
                                   return_frame=None, records=[], samples={}, previous=None,
                                   baseline=None, previous_panel=None,
                                   origin=[(COLUMN - pet.width) // 2, 440 - pet.height]))

            # Native duration plus a finite 5s transition guard; never endless polling.
            limit = math.ceil((1 + max(s["native_seconds"] for s in states) + 5 + 1) * FPS)
            with av.open(str(video_path), "w") as video:
                stream = video.add_stream("libx264", rate=FPS)
                stream.width, stream.height = WIDTH, HEIGHT
                stream.pix_fmt = "yuv420p"
                stream.options = {"crf": "18", "preset": "fast"}
                for index in range(limit):
                    clock.now = 1000.0 + index / FPS
                    canvas = QImage(WIDTH, HEIGHT, QImage.Format_RGBA8888)
                    canvas.fill(background)
                    painter = QPainter(canvas)
                    try:
                        for column, (pet, state) in enumerate(zip(pets, states)):
                            if index == 0:
                                pet._last_tick = clock.now - 1 / FPS
                            pet._tick()
                            if index == FPS:
                                if not pet.play_idle_motion(state["name"]):
                                    raise RuntimeError("play_idle_motion rejected " + state["name"])
                            if pet._timer.isActive():
                                raise RuntimeError("Renderer unexpectedly started a timer")
                            active = pet.active_clip
                            if index >= FPS and state["return_frame"] is None and active != "idle:" + state["name"]:
                                state["return_frame"] = index
                                state["samples"]["motion_last"] = (index - 1, state["previous_panel"].copy())
                            returned = state["return_frame"]
                            phase = "resident" if index < FPS else ("return" if returned is not None else "motion")

                            # Render once into a transparent panel, then composite it
                            # unchanged. No painter.scale(), fitted bbox or pose edits.
                            layer = QImage(COLUMN, HEIGHT, QImage.Format_RGBA8888)
                            layer.fill(Qt.transparent)
                            lp = QPainter(layer)
                            try:
                                pet.draw(lp, *state["origin"])
                            finally:
                                lp.end()
                            pixels = rgba_pixels(layer, np)
                            row = measure(pixels, state["previous"], state["baseline"], np)
                            row.update(frame=index, seconds=index / FPS, phase=phase,
                                       active_clip=active, clip_frame=float(pet._clip_frame))
                            state["records"].append(row)
                            state["previous"] = pixels
                            if index == FPS - 1:
                                state["baseline"] = row
                            panel = QImage(COLUMN, HEIGHT, QImage.Format_RGBA8888)
                            panel.fill(background)
                            # Fixed reference line uses the measured resident feet.
                            # It never follows the animated pose.
                            foot_line = state["baseline"]["foot_y"] if state["baseline"] else 440
                            annotate(panel, state["name"], phase, index / FPS, foot_line)
                            pp = QPainter(panel)
                            try:
                                pp.drawImage(0, 0, layer)
                            finally:
                                pp.end()
                            painter.drawImage(column * COLUMN, 0, panel)
                            targets = {"resident": FPS - 1, "entry_first": FPS,
                                       "entry_0.15s": FPS + math.ceil(.15 * FPS),
                                       "motion_middle": FPS + round(state["native_seconds"] * FPS / 2)}
                            if returned is not None:
                                targets.update(return_first=returned,
                                               **{"return_0.15s": returned + math.ceil(.15 * FPS),
                                                  "restored": returned + FPS - 1})
                            for moment, target in targets.items():
                                if index == target:
                                    state["samples"][moment] = (index, panel.copy())
                            state["previous_panel"] = panel
                    finally:
                        painter.end()
                    rgb = rgba_pixels(canvas, np)[:, :, :3].copy()
                    frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
                    frame.pts, frame.time_base = index, Fraction(1, FPS)
                    for packet in stream.encode(frame):
                        video.mux(packet)
                    if all(s["return_frame"] is not None and index >= s["return_frame"] + FPS - 1 for s in states):
                        break
                else:
                    raise RuntimeError("Idle motion failed to finish within native duration + 5s guard")
                for packet in stream.encode():
                    video.mux(packet)

            # Four columns, eight rows; full-size panels keep silhouettes readable.
            # Width is 1280 (<1920). Captions identify each column's own event time.
            sheet = QImage(WIDTH, len(MOMENTS) * (HEIGHT + 28), QImage.Format_RGBA8888)
            sheet.fill(background)
            sp = QPainter(sheet)
            try:
                sp.setFont(QFont("Arial", 10))
                sp.setPen(QColor("#263445"))
                for row_index, moment in enumerate(MOMENTS):
                    for column, state in enumerate(states):
                        frame_index, sample = state["samples"][moment]
                        x, y = column * COLUMN, row_index * (HEIGHT + 28)
                        sp.drawText(x + 10, y + 20, "%s | f%d (%.3fs)" % (moment, frame_index, frame_index / FPS))
                        sp.drawImage(x, y + 28, sample)
            finally:
                sp.end()
            for path in (OUTPUT / "contact.png", archive / "contact.png"):
                if not sheet.save(str(path)):
                    raise RuntimeError("Could not save contact sheet: " + str(path))
            metrics = dict(label=args.label, fps=FPS, canvas=[WIDTH, HEIGHT],
                           alpha_cutoff=ALPHA_CUTOFF, bbox_coordinates="column-local, inclusive xyxy",
                           adjacent_difference="premultiplied RGBA, alpha < cutoff zeroed, union mask",
                           requested_offset_seconds=.15, sampled_offset_seconds=math.ceil(.15 * FPS) / FPS,
                           encoded_frames=index + 1, video_seconds=(index + 1) / FPS,
                           video=str(video_path), contact_size=[sheet.width(), sheet.height()], actions={})
            for state in states:
                records = state["records"]
                metrics["actions"][state["name"]] = dict(
                    native_fps=state["native_fps"], native_frames=state["native_frames"],
                    native_seconds=state["native_seconds"], origin=state["origin"],
                    entry_frame=FPS, return_frame=state["return_frame"],
                    observed_motion_seconds=(state["return_frame"] - FPS) / FPS,
                    resident_tail_seconds=(index - state["return_frame"] + 1) / FPS,
                    keyframes={key: value[0] for key, value in state["samples"].items()},
                    summary=summarize(records, state["return_frame"], np), frames=records)
            serialized = json.dumps(metrics, ensure_ascii=False, indent=2, allow_nan=False)
            for path in (OUTPUT / "metrics.json", archive / "metrics.json"):
                path.write_text(serialized + "\n", encoding="utf-8")
            print(json.dumps(dict(label=args.label, video=str(video_path),
                                  contact=str(archive / "contact.png"),
                                  metrics=str(archive / "metrics.json"), frames=index + 1)))
        finally:
            for pet in pets:
                pet.stop()
    # Keep QApplication alive until every QPixmap/render operation has finished.
    _ = app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
