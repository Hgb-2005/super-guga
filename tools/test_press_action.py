import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from core.computer_control import ComputerControl,_key_values


class KeyNameTests(unittest.TestCase):
    def test_enter_aliases(self):
        for name in ('enter','return','回车','发送','确认','确定'):
            with self.subTest(name=name):
                self.assertEqual(_key_values(name),[13])

    def test_escape_and_tab(self):
        self.assertEqual(_key_values('esc'),[27])
        self.assertEqual(_key_values('escape'),[27])
        self.assertEqual(_key_values('tab'),[9])

    def test_combinations_keep_order(self):
        self.assertEqual(_key_values('ctrl+a'),[17,65])
        self.assertEqual(_key_values('ctrl+shift+end'),[17,16,35])

    def test_unknown_key_is_rejected_with_guidance(self):
        with self.assertRaises(ValueError) as ctx:
            _key_values('hyperkey')
        self.assertIn('不支持的组合键',str(ctx.exception))


class PressActionTests(unittest.TestCase):
    def setUp(self):
        class Access:enabled=True
        self.control=ComputerControl.__new__(ComputerControl)
        self.control.access=Access();self.control.observation=None
        self.control._uia_backend=None

    def test_press_is_accepted_without_an_observation_id(self):
        calls=[]
        with patch('core.computer_control._send_keys',side_effect=lambda values:calls.append(values)), \
             patch('win32gui.GetForegroundWindow',return_value=4242):
            result=self.control._execute(dict(action='press',keys='enter'))
        self.assertTrue(result['input_sent'])
        self.assertEqual(calls,[[13]])
        self.assertEqual(result['action'],'hotkey')
        self.assertTrue(result['needs_observe'])

    def test_press_defaults_to_enter_when_no_key_is_given(self):
        calls=[]
        with patch('core.computer_control._send_keys',side_effect=lambda values:calls.append(values)), \
             patch('win32gui.GetForegroundWindow',return_value=4242):
            self.control._execute(dict(action='press'))
        self.assertEqual(calls,[[13]])

    def test_press_accepts_the_key_field_models_actually_send(self):
        for payload in (dict(action='press',keys='enter'),dict(action='press',key='enter'),dict(action='press',text='enter')):
            with self.subTest(payload=payload):
                calls=[]
                with patch('core.computer_control._send_keys',side_effect=lambda values:calls.append(values)), \
                     patch('win32gui.GetForegroundWindow',return_value=4242):
                    self.control._execute(dict(payload))
                self.assertEqual(calls,[[13]])

    def test_press_without_a_foreground_window_refuses(self):
        with patch('core.computer_control._send_keys') as send, \
             patch('win32gui.GetForegroundWindow',return_value=0):
            with self.assertRaises(ValueError) as ctx:
                self.control._execute(dict(action='press',keys='enter'))
        send.assert_not_called()
        self.assertIn('没有前台窗口',str(ctx.exception))

    def test_unknown_action_lists_what_is_supported(self):
        with self.assertRaises(ValueError) as ctx:
            self.control._execute(dict(action='teleport'))
        message=str(ctx.exception)
        self.assertIn('未知屏幕操作',message)
        self.assertIn('press',message)
        self.assertIn('hotkey',message)


if __name__=='__main__':
    unittest.main()
