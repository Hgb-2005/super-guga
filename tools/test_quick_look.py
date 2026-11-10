import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from core.quick_look import look,describe,_window_info


WINDOW_PATCHES=lambda: (
    patch('win32gui.IsWindow',return_value=True),
    patch('win32gui.GetWindowText',return_value='测试窗口'),
)


class QuickLookChannelTests(unittest.TestCase):
    def test_invalid_window_is_reported_without_guessing(self):
        with patch('win32gui.IsWindow',return_value=False):
            result=look('屏幕上是什么',hwnd=999999999)
        self.assertFalse(result['ok'])
        self.assertIn('elapsed_ms',result)

    def test_controls_answer_first_and_no_screenshot_is_taken(self):
        a,b=WINDOW_PATCHES()
        with a,b, \
             patch('core.quick_look._uia_lines',return_value='Button：最大化\nButton：最小化'), \
             patch('core.quick_look.grab_window') as grab:
            result=look('有什么按钮',hwnd=1234)
        self.assertTrue(result['ok'])
        self.assertEqual(result['level'],'uia')
        self.assertEqual(result['cost'],'free')
        grab.assert_not_called()

    def test_local_ocr_answers_before_any_cloud_call(self):
        a,b=WINDOW_PATCHES()
        with a,b, \
             patch('core.quick_look._uia_lines',return_value=None), \
             patch('core.quick_look.grab_window',return_value=object()), \
             patch('core.quick_look._local_ocr',return_value='这是一段足够长的屏幕文字内容，用于验证本地识字通道'), \
             patch('core.quick_look._cloud_ocr') as cloud, \
             patch('core.quick_look._cloud_vision') as vision:
            result=look('屏幕上写了什么',hwnd=1234)
        self.assertEqual(result['level'],'ocr')
        self.assertEqual(result['cost'],'free')
        cloud.assert_not_called();vision.assert_not_called()

    def test_cloud_ocr_takes_over_when_local_ocr_is_missing(self):
        a,b=WINDOW_PATCHES()
        with a,b, \
             patch('core.quick_look._uia_lines',return_value=None), \
             patch('core.quick_look.grab_window',return_value=object()), \
             patch('core.quick_look._local_ocr',side_effect=RuntimeError('本机没有识字组件')), \
             patch('core.quick_look._cloud_ocr',return_value='云端读到的文字内容足够长，可以直接回答用户的问题'), \
             patch('core.quick_look._cloud_vision') as vision:
            result=look('屏幕上写了什么',hwnd=1234)
        self.assertEqual(result['level'],'cloud_ocr')
        self.assertEqual(result['cost'],'cloud')
        vision.assert_not_called()
        self.assertTrue(any('本机识字不可用' in note for note in result['notes']))

    def test_vision_is_the_last_resort(self):
        a,b=WINDOW_PATCHES()
        with a,b, \
             patch('core.quick_look._uia_lines',return_value=None), \
             patch('core.quick_look.grab_window',return_value=object()), \
             patch('core.quick_look._local_ocr',return_value=''), \
             patch('core.quick_look._cloud_ocr',return_value=''), \
             patch('core.quick_look._cloud_vision',return_value=('这是一个代码编辑器窗口',dict(model='qwen3-vl-plus'))):
            result=look('这个界面在做什么',hwnd=1234)
        self.assertEqual(result['level'],'vision')
        self.assertEqual(result['cost'],'cloud')
        self.assertIn('代码编辑器',result['text'])

    def test_cloud_can_be_refused_and_failure_is_explicit(self):
        a,b=WINDOW_PATCHES()
        with a,b, \
             patch('core.quick_look._uia_lines',return_value=None), \
             patch('core.quick_look.grab_window',return_value=object()), \
             patch('core.quick_look._local_ocr',return_value=''), \
             patch('core.quick_look._cloud_ocr') as cloud, \
             patch('core.quick_look._cloud_vision') as vision:
            result=look('这个界面在做什么',hwnd=1234,allow_cloud=False)
        self.assertFalse(result['ok'])
        cloud.assert_not_called();vision.assert_not_called()

    def test_unreadable_window_never_invents_content(self):
        a,b=WINDOW_PATCHES()
        with a,b, \
             patch('core.quick_look._uia_lines',return_value=None), \
             patch('core.quick_look.grab_window',side_effect=RuntimeError('窗口没有画出内容')), \
             patch('core.quick_look.grab_screen',side_effect=RuntimeError('窗口没有可见区域')):
            result=look('屏幕上是什么',hwnd=1234)
        self.assertFalse(result['ok'])
        self.assertNotIn('text',result)


class WindowInfoTests(unittest.TestCase):
    def test_window_info_carries_handle_and_title(self):
        with patch('win32gui.GetWindowText',return_value='记事本'):
            info=_window_info(99)
        self.assertEqual(info['hwnd'],99)
        self.assertEqual(info['title'],'记事本')

    def test_describe_uses_the_same_channel(self):
        a,b=WINDOW_PATCHES()
        with a,b, \
             patch('core.quick_look._uia_lines',return_value='Text：你好'), \
             patch('core.quick_look.grab_window') as grab:
            result=describe(hwnd=1234)
        self.assertEqual(result['level'],'uia')
        grab.assert_not_called()


if __name__=='__main__':
    unittest.main()
