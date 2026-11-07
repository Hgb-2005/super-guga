"""Duplex capture, mute isolation and safe echo-processor fallback."""
import os
import unittest
from unittest.mock import Mock
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from launcher import _prepare_qt
_prepare_qt()
from PyQt5.QtWidgets import QApplication
from core.realtime_voice import RealtimeVoice,DEFAULTS
APP=QApplication.instance() or QApplication([])


class EchoCaptureTests(unittest.TestCase):
    def voice(self):
        voice=RealtimeVoice();voice._cfg=dict(DEFAULTS,headphones=False);voice._playing=True
        voice._echo_processor=Mock();voice._echo_processor.process.return_value=b'\x06\x00'*4
        voice._echo_processor.status.return_value=dict(engine='fixture')
        return voice

    def test_speaker_playback_keeps_processed_user_speech_live(self):
        voice=self.voice();voice._capture(b'\x03\x00'*4,4,None,None)
        voice._echo_processor.process.assert_called_once_with(b'\x03\x00'*4)
        self.assertEqual(voice._input.get_nowait(),b'\x06\x00'*4)
        self.assertEqual(voice.audio_path_status()['mode'],'speaker_aec3_full_duplex')

    def test_explicit_mute_remains_silent_even_if_dsp_returns_nonzero(self):
        voice=self.voice();voice._muted=True;voice._capture(b'\x03\x00'*4,4,None,None)
        self.assertFalse(any(voice._input.get_nowait()))

    def test_aec_failure_preserves_connection_and_returns_to_echo_protection(self):
        voice=self.voice();voice._connected=True;voice._thread=Mock();voice._thread.is_alive.return_value=True
        echo=voice._echo_processor;echo.process.side_effect=RuntimeError('fixture')
        voice._capture(b'\x03\x00'*4,4,None,None)
        self.assertIsNone(voice._echo_processor);self.assertTrue(voice.connected);echo.close.assert_called_once()
        self.assertFalse(any(voice._input.get_nowait()));voice._capture(b'\x03\x00'*4,4,None,None)
        self.assertFalse(any(voice._input.get_nowait()));self.assertEqual(voice._echo_error,'RuntimeError')

    def test_exact_played_samples_become_echo_reference(self):
        voice=self.voice();voice._pcm.append(b'\x07\x00'*4);output=bytearray(12)
        voice._playback(output,6,None,None)
        voice._echo_processor.render.assert_called_once_with(bytes(output))
        self.assertEqual(bytes(output),b'\x07\x00'*4+b'\0'*4)

    def test_verified_speaker_gate_output_is_not_muted_again_during_playback(self):
        voice=self.voice();voice._speaker_output(b'\x04\x00'*4)
        self.assertEqual(voice._input.get_nowait(),b'\x04\x00'*4)

    def test_legacy_half_duplex_is_explicit_when_aec_is_absent(self):
        voice=RealtimeVoice();voice._cfg=dict(DEFAULTS);voice._playing=True
        voice._capture(b'\x03\x00'*4,4,None,None)
        self.assertFalse(any(voice._input.get_nowait()))
        self.assertEqual(voice.audio_path_status()['mode'],'speaker_echo_protected')


if __name__=='__main__':unittest.main()
