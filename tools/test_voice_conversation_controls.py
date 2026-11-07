"""Offline tests for long-call persistence and voice conversation controls."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from core.fast_commands import match
from core.realtime_voice import call_expiry_reason, load_settings


class LongCallLifetimeTests(unittest.TestCase):
    def test_saved_zero_idle_is_migrated_to_thirty_minutes(self):
        for stored, expected in ((0, 30), (3, 5), (60, 30), (20, 20)):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'ai_config.json'
                path.write_text(json.dumps({'realtime_voice': {'idle_minutes': stored}}), encoding='utf-8')
                with patch('core.realtime_voice.CONFIG_PATH', path):
                    self.assertEqual(load_settings()['idle_minutes'], expected)

    def test_zero_means_keep_open_until_user_stops(self):
        cfg = dict(idle_minutes=0, max_call_minutes=0)
        self.assertEqual(call_expiry_reason(cfg, 10_000, 0, 0), '')

    def test_missing_or_bad_values_do_not_expire(self):
        self.assertEqual(call_expiry_reason({}, 10_000, 0, 0), '')
        self.assertEqual(call_expiry_reason(dict(idle_minutes='x', max_call_minutes=None), 10_000, 0, 0), '')

    def test_positive_limits_still_apply(self):
        self.assertEqual(call_expiry_reason(dict(max_call_minutes=1), 61, 0, 60), 'max')
        self.assertEqual(call_expiry_reason(dict(idle_minutes=10), 601, 0, 0), 'idle')
        self.assertEqual(call_expiry_reason(dict(idle_minutes=10), 599, 0, 0), '')


class ConversationControlPhraseTests(unittest.TestCase):
    def test_close_phrases_stop_the_long_call(self):
        for phrase in ('闭嘴', '闭嘴吧', '安静', '安静点', '别说话', '别出声', '不要说话', '结束通话', '别说了',
                       '你可以退下了', '退下吧', '你退下吧', '你可以去休息了', '先聊到这里'):
            parsed = match(phrase)
            self.assertTrue(parsed.get('matched'), phrase)
            self.assertEqual(parsed.get('action'), 'stop_call', phrase)

    def test_heart_phrases_trigger_heart_gesture(self):
        for phrase in ('比心', '双手比心', '比个心', '给你比心'):
            parsed = match(phrase)
            self.assertEqual((parsed.get('action'), parsed.get('kind'), parsed.get('target')),
                             ('performance', 'gesture', 'idle_heart'), phrase)

    def test_singing_still_maps_to_qingtian(self):
        parsed = match('唱晴天')
        self.assertEqual((parsed.get('action'), parsed.get('kind'), parsed.get('target')), ('performance', 'sing', '晴天'))
        self.assertEqual(match('唱首歌').get('target'), '晴天')

    def test_fast_open_commands_still_match(self):
        self.assertEqual(match('打开浏览器').get('action'), 'open_app')
        self.assertEqual(match('打开记事本').get('action'), 'open_app')


if __name__ == '__main__':
    unittest.main(verbosity=2)
