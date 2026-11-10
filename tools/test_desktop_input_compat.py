"""Readable older edit controls and uncertainty before/after a write."""
import sys
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from core.uia_backend import UIABackend


class OlderInputTests(unittest.TestCase):
    def setUp(self):
        self.backend=UIABackend.__new__(UIABackend)
        self.backend.lock=threading.RLock();self.backend.revisions={}
        self.backend.Patterns={'set_value':NS(Pattern='value')}
        self.element=Mock();self.element.Current.IsEnabled=True;self.element.Current.IsPassword=False
        self.item=dict(ref='field',type='Edit')
        self.backend._resolve=Mock(return_value=(self.element,self.item))
        self.backend._rect=Mock(return_value=None)
        self.automation=NS(LegacyIAccessiblePattern=NS(Pattern='legacy'),
            TextPattern=NS(Pattern='text',IsReadOnlyAttribute='readonly'))

    def test_legacy_edit_can_be_written_and_read_without_keyboard(self):
        pattern=Mock();pattern.Current.State=0;pattern.Current.Value='原文'
        def write(value):pattern.Current.Value=value
        pattern.SetValue.side_effect=write
        # Unsupported native patterns are ordinary provider exceptions, not
        # a RuntimeError raised by our explicit read-only check.
        def provider(key):
            if key=='legacy':return pattern
            raise LookupError('unsupported pattern')
        self.element.GetCurrentPattern.side_effect=provider
        with patch.dict(sys.modules,{'System.Windows.Automation':self.automation}), \
                patch.object(self.backend,'_replace_text_by_keys') as keyboard:
            result=self.backend.act(7,'set_value',ref='field',value='新文字')
        self.assertTrue(result['verified']);pattern.SetValue.assert_called_once_with('新文字')
        keyboard.assert_not_called()

    def test_legacy_readonly_edit_is_not_written(self):
        pattern=Mock();pattern.Current.State=0x40;pattern.Current.Value='保留'
        def provider(key):
            if key=='legacy':return pattern
            raise LookupError()
        self.element.GetCurrentPattern.side_effect=provider
        with patch.dict(sys.modules,{'System.Windows.Automation':self.automation}), \
                self.assertRaisesRegex(RuntimeError,'只读'):
            self.backend.act(7,'set_value',ref='field',value='覆盖')
        pattern.SetValue.assert_not_called()

    def test_failed_legacy_write_is_never_followed_by_keyboard(self):
        pattern=Mock();pattern.Current.State=0;pattern.Current.Value='保留'
        pattern.SetValue.side_effect=OSError('write may have reached provider')
        def provider(key):
            if key=='legacy':return pattern
            raise LookupError()
        self.element.GetCurrentPattern.side_effect=provider
        with patch.dict(sys.modules,{'System.Windows.Automation':self.automation}), \
                patch.object(self.backend,'_replace_text_by_keys') as keyboard, \
                self.assertRaisesRegex(RuntimeError,'未自动重试'):
            self.backend.act(7,'set_value',ref='field',value='覆盖')
        self.assertEqual(pattern.SetValue.call_count,1);keyboard.assert_not_called()

    def test_text_only_edit_uses_keyboard_once_and_checks_actual_text(self):
        text=Mock();text.DocumentRange.GetAttributeValue.return_value=False
        text.DocumentRange.GetText.return_value='原文'
        def provider(key):
            if key=='text':return text
            raise LookupError()
        self.element.GetCurrentPattern.side_effect=provider
        def keyboard(hwnd,element,value):text.DocumentRange.GetText.return_value=value
        with patch.dict(sys.modules,{'System.Windows.Automation':self.automation}), \
                patch.object(self.backend,'_replace_text_by_keys',side_effect=keyboard) as send:
            result=self.backend.act(7,'set_value',ref='field',value='新文字')
        self.assertTrue(result['verified']);send.assert_called_once_with(7,self.element,'新文字')

    def test_multiple_distinct_fields_are_not_collapsed_into_one(self):
        edit=dict(type='Edit',enabled=True,password=False,rect=dict(left=1,top=1,right=5,bottom=5))
        combo=dict(type='ComboBox',enabled=True,password=False,rect=dict(left=10,top=1,right=15,bottom=5))
        self.backend._find=Mock(side_effect=[[(object(),edit)],[(object(),combo)]])
        with self.assertRaisesRegex(RuntimeError,'不唯一'):self.backend.input_target(7)

    def test_combo_wrapping_the_same_edit_is_one_field(self):
        edit=dict(type='Edit',enabled=True,password=False,rect=dict(left=2,top=2,right=5,bottom=5))
        combo=dict(type='ComboBox',enabled=True,password=False,rect=dict(left=1,top=1,right=6,bottom=6))
        self.backend._find=Mock(side_effect=[[(object(),edit)],[(object(),combo)]])
        self.assertIs(self.backend.input_target(7)['element'],edit)

    def test_trailing_returns_do_not_hide_a_truncated_draft(self):
        text=Mock();text.DocumentRange.GetAttributeValue.return_value=False
        text.DocumentRange.GetText.return_value='A'*3999+'\r\r'
        def provider(key):
            if key=='text':return text
            raise LookupError()
        self.element.GetCurrentPattern.side_effect=provider
        with patch.dict(sys.modules,{'System.Windows.Automation':self.automation}):
            actual,_,_=self.backend._input_value(self.element)
        self.assertEqual(len(actual),4001)
        self.assertTrue(len(actual)>4000,'focused() must flag this draft as truncated before append')


if __name__=='__main__':unittest.main()
