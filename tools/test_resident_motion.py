"""Real rendering, settled exits and priority control for the four native clips."""
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import time
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
from launcher import _prepare_qt
_prepare_qt()
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QImage,QPainter
from PyQt5.QtCore import Qt
from ui.sprite_animator import SpriteAnimator
from core.avatar_review import approved_action

APP=QApplication.instance() or QApplication([])
ROOT=Path(__file__).resolve().parents[1]
PACK=ROOT/'resources/actions/resident-motion-v2'


def render(anim):
    image=QImage(anim.width,anim.height,QImage.Format_RGBA8888);image.fill(Qt.transparent)
    painter=QPainter(image);anim.draw(painter,0,0);painter.end()
    pointer=image.bits();pointer.setsize(image.sizeInBytes())
    return np.frombuffer(pointer,dtype=np.uint8).reshape(image.height(),image.width(),4).copy()


class ResidentMotionTests(unittest.TestCase):
    def setUp(self):
        self.anim=SpriteAnimator(str(ROOT/'resources'),245);self.anim.stop()
        self.anim._idle_action_countdown=9999;self.anim._idle_expr_countdown=9999
    def tearDown(self):self.anim.stop()

    def test_all_four_packs_are_approved_and_share_actual_body_scale(self):
        spans=[]
        for name in ('doze','heart','pout','laugh'):
            self.assertTrue(approved_action(PACK/name/'frames'))
            meta=json.loads((PACK/name/'geometry.json').read_text(encoding='utf-8'))
            spans.append(meta['body_span']);self.assertEqual(meta['edge_clipped_frames'],0)
        self.assertLess(max(spans)-min(spans),1.)
        self.assertGreater(self.anim.width,self.anim._dw)

    def test_native_frames_do_not_inherit_breathing_rotation_or_old_track(self):
        for name in ('doze','heart','pout','laugh'):
            with self.subTest(name=name):
                self.anim.play_idle_motion(name);before=render(self.anim)
                self.anim.breath_y=20;self.anim._track_squash=1.25;self.anim._track_dy=-40
                self.anim.body_sway_x=12;self.anim.anim_rotate=18;self.anim.head_yaw=1
                after=render(self.anim)
                self.assertTrue(np.array_equal(before,after))

    def test_natural_end_keeps_the_last_actually_rendered_pose(self):
        self.anim.play_idle_motion('heart')
        self.anim._clip_frame=len(self.anim.clips['idle:heart']['frames'])-1
        before=render(self.anim)
        with patch('ui.sprite_animator.time.monotonic',return_value=100.):
            self.anim.stop_clip(natural=True);after=render(self.anim)
        self.assertTrue(np.array_equal(before,after));self.assertIsNotNone(self.anim._idle_rest)

    def test_speech_and_explicit_expression_release_old_settled_pose(self):
        self.anim.play_idle_motion('doze');render(self.anim);self.anim.stop_clip(natural=True)
        self.anim.set_speaking(True);self.assertIsNone(self.anim._idle_rest)
        self.anim.set_speaking(False);self.anim.play_idle_motion('heart');render(self.anim)
        self.anim.stop_clip(natural=True);self.anim.set_expression('sad')
        self.assertIsNone(self.anim._idle_rest)

    def test_blockers_prevent_manual_or_automatic_reentry(self):
        for field in ('_speaking','_being_dragged','_music_stage_active'):
            with self.subTest(field=field):
                setattr(self.anim,field,True)
                self.assertFalse(self.anim.play_idle_motion('heart'))
                self.anim._idle_action_countdown=0
                with patch('ui.sprite_animator.random.random',return_value=0.):self.anim._tick()
                self.assertFalse((self.anim.active_clip or '').startswith('idle:'))
                setattr(self.anim,field,False)

    def test_laugh_closing_frames_are_existing_source_pixels(self):
        meta=json.loads((PACK/'laugh/geometry.json').read_text(encoding='utf-8'))
        indices=meta['settling_source_indices'];self.assertEqual(indices[-1],0)
        for offset,index in enumerate(indices):
            self.assertEqual((PACK/'laugh/frames'/f'{241+offset:04}.png').read_bytes(),
                (PACK/'laugh/frames'/f'{index:04}.png').read_bytes())

    def test_displayed_canvas_preserves_native_body_at_multiple_sizes(self):
        for width in (147,245,300):
            animator=SpriteAnimator(str(ROOT/'resources'),width);animator.stop()
            try:
                for name in ('doze','heart','pout','laugh'):
                    animator.play_idle_motion(name);image=render(animator)
                    mask=image[:,:,3]>=128;ys,xs=np.where(mask)
                    self.assertGreater(xs.min(),0);self.assertLess(xs.max(),animator.width-1)
                    self.assertGreater(ys.min(),0);self.assertLess(ys.max(),animator.height-1)
            finally:animator.stop()


if __name__=='__main__':unittest.main()
