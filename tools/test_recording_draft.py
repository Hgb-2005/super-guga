"""Finite local recording lifecycle checks, without microphone or network."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import sys,time,threading,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from launcher import _prepare_qt
_prepare_qt()
from PyQt5.QtWidgets import QApplication
from core.short_voice import ShortVoice

APP=QApplication.instance() or QApplication([])


class Recorder:
    available=True;error=None;level=.5
    def __init__(self):self.starts=0;self.stops=0
    def start(self):self.starts+=1
    def stop(self):self.stops+=1;return '打开 浏览器'


class RecordingDraftTests(unittest.TestCase):
    def setUp(self):
        self.recorder=Recorder();self.voice=ShortVoice(factory=lambda:self.recorder,max_seconds=.15)
        self.sent=[];self.states=[];self.failures=[]
        self.voice.transcript.connect(self.sent.append);self.voice.state.connect(self.states.append)
        self.voice.failed.connect(self.failures.append)

    def pump(self,predicate,seconds=2):
        until=time.monotonic()+seconds
        while not predicate() and time.monotonic()<until:APP.processEvents();time.sleep(.005)
        APP.processEvents();self.assertTrue(predicate())

    def tearDown(self):
        self.voice.stop(cancel=True)
        if self.voice._worker:self.voice._worker.join(.6)
        APP.processEvents()

    def test_timeout_keeps_draft_until_send(self):
        self.voice.start();self.pump(lambda:self.voice.phase=='ready')
        self.assertEqual(self.sent,[]);self.assertTrue(self.voice.active)
        self.assertEqual(self.recorder.stops,1)
        self.voice.stop();self.assertEqual(self.sent,['打开浏览器'])
        self.assertFalse(self.voice.active)

    def test_explicit_send_emits_once_and_releases_mic(self):
        self.voice.start();self.pump(lambda:self.voice.phase=='recording')
        self.voice.stop();self.voice.stop()
        self.pump(lambda:not self.voice.active)
        self.assertEqual(self.sent,['打开浏览器']);self.assertEqual(self.recorder.stops,1)

    def test_cancel_while_recording_never_sends(self):
        self.voice.start();self.pump(lambda:self.voice.phase=='recording')
        self.voice.stop(cancel=True);self.pump(lambda:not self.voice._worker.is_alive())
        self.assertEqual(self.sent,[]);self.assertEqual(self.recorder.stops,1)
        self.assertEqual(self.voice.phase,'idle')

    def test_cancel_ready_draft_discards_it(self):
        self.voice.start();self.pump(lambda:self.voice.phase=='ready')
        self.voice.stop(cancel=True);self.voice.stop()
        self.assertEqual(self.sent,[]);self.assertEqual(self.voice.draft,'')

    def test_cancel_preparing_never_opens_microphone(self):
        gate=threading.Event();recorder=self.recorder
        class SlowRecorder(Recorder):
            @property
            def available(self):gate.wait(.5);return True
        slow=SlowRecorder();self.voice.factory=lambda:slow
        self.voice.start();self.voice.stop(cancel=True);gate.set()
        self.pump(lambda:not self.voice._worker.is_alive())
        self.assertEqual(slow.starts,0);self.assertEqual(self.sent,[])

    def test_rapid_start_reuses_one_recorder(self):
        self.voice.start();self.voice.start();self.pump(lambda:self.voice.phase=='recording')
        self.assertEqual(self.recorder.starts,1)
        self.voice.stop(cancel=True);self.pump(lambda:not self.voice._worker.is_alive())
        self.voice.start();self.pump(lambda:self.recorder.starts==2)
        self.voice.stop();self.pump(lambda:not self.voice.active)
        self.assertEqual(self.sent,['打开浏览器'])

    def test_empty_or_failed_audio_is_not_submitted(self):
        self.recorder.stop=lambda:''
        self.voice.start();self.pump(lambda:self.voice.phase=='recording');self.voice.stop()
        self.pump(lambda:not self.voice.active)
        self.assertFalse(self.sent);self.assertTrue(self.failures)


if __name__=='__main__':unittest.main()
