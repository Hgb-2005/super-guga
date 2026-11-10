"""Offline contract tests; never inspect a real window, config, or send input.

Run: python -B tools/test_desktop_interaction.py
"""
import copy
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.browser_interaction import parse_step
from core.desktop_interaction import DesktopInteraction, parse_desktop_step, window_fingerprint
from core.uia_backend import UIAUnavailable


def element(ref='save', name='保存', kind='Button', **fields):
    result = dict(ref=ref, name=name, type=kind, rect=dict(left=20, top=40, right=200, bottom=80),
                  enabled=True, offscreen=False, password=False, focused=False)
    result.update(fields)
    return result


def identity(**fields):
    result = dict(hwnd=123, pid=567, process='notepad.exe', rect=[0, 0, 1000, 800],
                  title='测试文档', class_name='Notepad', process_started=42.0)
    result.update(fields)
    return result


def visual(window=None, **fields):
    window = window or identity()
    result = dict(source='visual', name='保存', rect=dict(left=20, top=40, right=200, bottom=80),
                  window_fingerprint={k: copy.deepcopy(window[k]) for k in ('hwnd', 'pid', 'process', 'rect', 'title')},
                  observed_at=time.monotonic(), frame_digest='fake-frame-digest')
    result.update(fields)
    return result


class ParseTests(unittest.TestCase):
    def test_reuses_legacy_parser_and_keeps_alias(self):
        for text in ('点击保存按钮', '输入你好', '在搜索框里输入你好', '追加输入后缀',
                     '向下滚动', '刷新页面', '聚焦地址栏'):
            with self.subTest(text=text):
                old, new = parse_step(text), parse_desktop_step(text)
                self.assertEqual(new['action'], 'desktop_step')
                self.assertEqual(new['step_action'], old['browser_action'])
                self.assertEqual(new['browser_action'], old['browser_action'])

    def test_scoped_clicks_use_exact_target(self):
        for scope in ('窗口里的', '软件中的', '屏幕里的', '程序内的', '应用上的'):
            with self.subTest(scope=scope):
                result = parse_desktop_step('点击' + scope + '保存按钮')
                self.assertEqual((result['step_action'], result['target']), ('click', '保存'))

    def test_lists_and_chinese_ordinals(self):
        for text in ('列出可点选项', '看看能点什么'):
            self.assertEqual(parse_desktop_step(text)['step_action'], 'list_targets')
        for text, index in (('点击第2项', 2), ('点击2号', 2), ('点二号', 2), ('点第十二项', 12), ('点击二十号', 20)):
            self.assertEqual(parse_desktop_step(text)['index'], index)

    def test_new_keys_and_phrasings(self):
        for text, action in (('按Esc', 'escape'), ('按一下ESC键', 'escape'), ('按取消键', 'escape'),
                             ('按Tab', 'tab'), ('按制表键', 'tab'), ('切换焦点', 'tab'),
                             ('按 Ctrl+f', 'find'), ('打开查找', 'find'),
                             ('Ctrl+a', 'select_all'), ('全选内容', 'select_all')):
            with self.subTest(text=text):
                self.assertEqual(parse_desktop_step(text)['step_action'], action)

    def test_literal_input_keeps_inner_whitespace_punctuation_and_case(self):
        text = '  ＡbC，  $HOME; `hello`\n然后点击保存  '
        raw = '输入 “' + text + '”'
        parsed = parse_desktop_step('输入 “normalized”', raw)
        self.assertEqual(parsed['target'], text)
        self.assertEqual(parse_desktop_step('在搜索框里输入 “  AbC  ”')['text'], '  AbC  ')
        self.assertEqual(parse_desktop_step('输入：你好')['target'], '你好')

    def test_input_method_questions_stay_questions_and_quoted_text_is_data(self):
        for text in ('在搜索框里输入法怎么切换', '输入法如何切换', '请在搜索框里输入法怎么切换'):
            self.assertIsNone(parse_desktop_step(text))
        self.assertEqual(parse_desktop_step('输入“输入法怎么切换”')['target'], '输入法怎么切换')
        self.assertEqual(parse_desktop_step('在搜索框里输入“输入法怎么切换”')['text'], '输入法怎么切换')

    def test_compound_unquoted_input_is_a_local_rejection(self):
        for text in ('输入你好然后点击保存', '在搜索框里输入猫接着点搜索'):
            self.assertEqual(parse_desktop_step(text)['step_action'], 'unsupported')
        self.assertEqual(parse_desktop_step('输入“你好然后点击保存”')['target'], '你好然后点击保存')

    def test_unsupported_actions_do_not_fall_through(self):
        for text in ('双击保存', '拖动图标', '右键点击保存'):
            self.assertEqual(parse_desktop_step(text)['step_action'], 'unsupported')
        for text in ('不要点击保存', '怎么点击保存', '今天什么天气'):
            self.assertIsNone(parse_desktop_step(text))


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.access = patch('core.desktop_access.DesktopAccess.enabled', new_callable=PropertyMock, return_value=True)
        self.access.start()
        self.addCleanup(self.access.stop)
        self.window = identity()
        self.items = [element(), element('edit', '内容', 'Edit', focused=True, value='原文')]
        self.uia = Mock(spec=['read', 'focused', 'snapshot', 'page_input', 'input_target', 'act', 'warmup', 'close'])
        self.uia.read.side_effect = self.read
        self.uia.focused.side_effect = lambda hwnd: dict(hwnd=hwnd, element=copy.deepcopy(self.items[1]))
        self.uia.snapshot.side_effect = lambda hwnd, **kw: dict(hwnd=hwnd, elements=copy.deepcopy(self.items))
        self.uia.act.return_value = dict(verified=True, input_sent=True, fallback=False)
        self.browser = Mock()
        self.browser.execute.return_value = dict(ok=True, action_sent=True, verified=False)
        self.obj = DesktopInteraction(uia=self.uia, browser=self.browser)
        self.obj._guard = Mock(side_effect=lambda hwnd=None: copy.deepcopy(self.window))
        self.obj._mouse_click = Mock()
        self.obj._send_keys = Mock()
        self.obj._scroll = Mock()

    def read(self, hwnd, **selector):
        matches = [item for item in self.items if
                   (not selector.get('ref') or item['ref'] == selector['ref']) and
                   (not selector.get('name') or item['name'] == selector['name']) and
                   (not selector.get('control_type') or item['type'] == selector['control_type'])]
        if len(matches) > 1:
            raise RuntimeError('UIA目标不唯一，请指定更精确的控件，未发送操作')
        if not matches:
            raise RuntimeError('UIA目标不存在或不可见，未发送操作')
        return dict(hwnd=hwnd, element=copy.deepcopy(matches[0]))

    def execute(self, text, **kw):
        command = parse_desktop_step(text) if isinstance(text, str) else text
        result = self.obj.execute(command, **kw)
        self.assertEqual(result['route'], 'local_desktop_step')
        self.assertGreaterEqual(result['dispatch_ms'], 0)
        return result

    def no_actions(self):
        self.uia.act.assert_not_called()
        self.obj._mouse_click.assert_not_called()
        self.obj._send_keys.assert_not_called()
        self.obj._scroll.assert_not_called()
        self.browser.execute.assert_not_called()

    def test_legacy_browser_click_works_on_desktop(self):
        result = self.execute(parse_step('点击保存'))
        self.assertTrue(result['ok'])
        self.assertTrue(result['silent'])
        self.uia.act.assert_called_once_with(123, 'invoke', ref='save')
        self.browser.execute.assert_not_called()

    def test_ended_call_rejects_its_pending_step(self):
        command=dict(parse_desktop_step('点击保存'),_can_execute=lambda:False)
        result=self.execute(command)
        self.assertFalse(result['ok']);self.assertFalse(result['action_sent']);self.no_actions()

    def test_cancel_while_resolving_stops_before_click(self):
        original=self.uia.read.side_effect
        def read_then_cancel(*args,**kwargs):
            result=original(*args,**kwargs);self.obj.cancel();return result
        self.uia.read.side_effect=read_then_cancel
        result=self.execute('点击保存')
        self.assertFalse(result['ok']);self.assertFalse(result['action_sent']);self.no_actions()

    def test_cancel_before_worker_starts_invalidates_generation(self):
        command=dict(parse_desktop_step('点击保存'),_step_generation=self.obj._generation)
        self.obj.cancel()
        result=self.execute(command)
        self.assertFalse(result['ok']);self.no_actions()

    def test_cancel_clears_numbers_and_allows_a_new_instruction(self):
        self.assertTrue(self.execute('列出可点选项')['ok'])
        self.obj.cancel()
        self.assertFalse(self.execute('点一号')['ok'])
        self.assertTrue(self.execute('点击保存')['ok'])

    def test_step_action_has_priority(self):
        result = self.execute(dict(step_action='tab', browser_action='refresh'))
        self.assertTrue(result['ok'])
        self.obj._send_keys.assert_called_once_with([9])
        self.browser.execute.assert_not_called()

    def test_browser_navigation_on_nonbrowser_never_sends_browser_keys(self):
        for text in ('刷新', '返回上一页', '新建标签页', '聚焦地址栏', '下一个标签页'):
            with self.subTest(text=text):
                result = self.execute(parse_step(text))
                self.assertFalse(result['ok'])
                self.assertFalse(result['action_sent'])
        self.no_actions()

    def test_browser_only_actions_delegate_once_and_keep_local_route(self):
        self.window['process'] = 'chrome.exe'
        result = self.execute('刷新页面')
        self.assertTrue(result['ok'])
        self.browser.execute.assert_called_once()
        self.assertEqual(self.browser.execute.call_args.kwargs['hwnd'], 123)
        self.no_uia_action()

    def no_uia_action(self):
        self.uia.act.assert_not_called()
        self.obj._send_keys.assert_not_called()

    def test_browser_delegate_unknown_exception_is_not_replayed(self):
        self.window['process'] = 'chrome.exe'
        self.browser.execute.side_effect = TimeoutError('结果不明')
        result = self.execute('刷新')
        self.assertFalse(result['ok'])
        self.assertIsNone(result['action_sent'])
        self.assertEqual(self.browser.execute.call_count, 1)

    def test_guard_failure_wrong_foreground_and_access_disabled(self):
        self.obj._guard.side_effect = RuntimeError('前台窗口已变化')
        self.assertFalse(self.execute('点击保存', hwnd=999)['action_sent'])
        self.no_actions()
        with patch('core.desktop_access.DesktopAccess.enabled', new_callable=PropertyMock, return_value=False):
            self.obj._guard.reset_mock()
            self.assertFalse(self.execute('点击保存')['ok'])
            self.obj._guard.assert_not_called()

    def test_foreground_change_between_read_and_dispatch_blocks_action(self):
        self.obj._guard.side_effect = [copy.deepcopy(self.window), RuntimeError('前台已变化')]
        result = self.execute('点击保存')
        self.assertFalse(result['action_sent'])
        self.no_actions()

    def test_same_hwnd_new_window_identity_blocks_action(self):
        self.obj._guard.side_effect = [copy.deepcopy(self.window), identity(process_started=99.0)]
        self.assertFalse(self.execute('点击保存')['action_sent'])
        self.no_actions()

    def test_uia_wrong_window_or_missing_ref_is_rejected(self):
        for response in (dict(hwnd=999, element=element()), dict(hwnd=123, element={'name': '保存'})):
            with self.subTest(response=response):
                self.uia.read.side_effect = None
                self.uia.read.return_value = response
                self.assertFalse(self.execute('点击保存')['action_sent'])
        self.no_actions()

    def test_duplicate_names_do_not_guess_or_use_visual(self):
        self.items.append(element('save2', '保存'))
        self.obj.target_resolver = Mock()
        result = self.execute('点击保存')
        self.assertFalse(result['action_sent'])
        self.assertIn('不唯一', result['error'])
        self.obj.target_resolver.assert_not_called()
        self.no_actions()

    def test_disabled_offscreen_password_controls_are_rejected(self):
        self.obj.target_resolver = Mock()
        for flags in (dict(enabled=False), dict(offscreen=True), dict(password=True), dict(enabled=None)):
            with self.subTest(flags=flags):
                self.items[0] = element(**flags)
                self.assertFalse(self.execute('点击保存')['action_sent'])
        self.obj.target_resolver.assert_not_called()
        self.no_actions()

    def test_focus_only_input_never_searches_for_another_edit(self):
        result = self.execute('输入 “  AbC，$HOME  ”')
        self.assertTrue(result['verified'])
        self.uia.act.assert_called_once_with(123, 'set_value', ref='edit', value='  AbC，$HOME  ')
        self.uia.read.assert_not_called()
        self.uia.page_input.assert_not_called()
        self.uia.input_target.assert_not_called()
        self.assertEqual(self.uia.focused.call_count, 2)

    def test_nonedit_focus_or_changed_focus_does_not_select_another_input(self):
        self.items[1] = element('edit', '内容', 'Button', focused=True)
        self.assertFalse(self.execute('输入你好')['action_sent'])
        self.items[1] = element('edit', '内容', 'Edit', focused=True)
        self.uia.focused.side_effect = [dict(element=self.items[1]),
                                        dict(element=element('another', '其他', 'Edit', focused=True))]
        self.assertFalse(self.execute('输入你好')['action_sent'])
        self.no_actions()

    def test_named_input_uses_exact_name_before_generic(self):
        self.items[1]['name'] = '搜索'
        self.assertTrue(self.execute('在搜索框里输入你好')['ok'])
        self.uia.input_target.assert_not_called()
        self.uia.page_input.assert_not_called()
        self.uia.act.assert_called_once_with(123, 'set_value', ref='edit', value='你好')

    def test_generic_input_routes_by_window_and_never_address_bar(self):
        self.uia.input_target.return_value = dict(element=self.items[1])
        self.uia.page_input.return_value = dict(element=self.items[1])
        for process, method in (('notepad.exe', self.uia.input_target), ('chrome.exe', self.uia.page_input)):
            with self.subTest(process=process):
                self.window['process'] = process
                result = self.execute('在搜索框里输入猫')
                self.assertTrue(result['ok'])
                method.assert_called_once_with(123, search=True)
        self.browser.execute.assert_not_called()

    def test_generic_duplicate_does_not_call_generic_selector(self):
        self.items += [element('s1', '搜索', 'Edit'), element('s2', '搜索', 'Edit')]
        self.assertFalse(self.execute('在搜索框里输入猫')['action_sent'])
        self.uia.input_target.assert_not_called()
        self.no_actions()

    def test_generic_selector_ambiguous_or_missing_never_uses_visual(self):
        self.obj.target_resolver = Mock()
        self.uia.input_target.side_effect = RuntimeError('输入框不唯一或不存在')
        result = self.execute('点击输入框')
        self.assertFalse(result['action_sent'])
        self.assertIn('不唯一或不存在', result['error'])
        self.obj.target_resolver.assert_not_called()
        self.no_actions()

    def test_missing_input_target_reports_integration_gap(self):
        del self.uia.input_target
        result = self.execute('点击输入框')
        self.assertFalse(result['action_sent'])
        self.assertIn('input_target', result['error'])
        self.no_actions()

    def test_append_clear_and_truncated_value(self):
        self.assertTrue(self.execute('追加输入后缀')['ok'])
        self.assertEqual(self.uia.act.call_args.kwargs['value'], '原文后缀')
        self.assertTrue(self.execute('清空输入框')['ok'])
        self.assertEqual(self.uia.act.call_args.kwargs['value'], '')
        self.uia.act.reset_mock()
        self.items[1]['value_truncated'] = True
        self.assertFalse(self.execute('追加输入后缀')['action_sent'])
        self.uia.act.assert_not_called()

    def test_readback_mismatch_and_action_timeout_are_uncertain_no_retry(self):
        for effect in ('mismatch', 'timeout'):
            with self.subTest(effect=effect):
                self.uia.act.reset_mock()
                self.uia.act.return_value = dict(verified=False, input_sent=True)
                self.uia.act.side_effect = TimeoutError('结果不确定，未自动重试') if effect == 'timeout' else None
                result = self.execute('输入你好')
                self.assertFalse(result['ok'])
                self.assertIsNone(result['action_sent'])
                self.assertEqual(self.uia.act.call_count, 1)

    def test_read_timeout_and_invoke_uncertainty_never_use_visual(self):
        self.obj.target_resolver = Mock()
        self.uia.read.side_effect = TimeoutError('UIA执行器超时或中断；结果不确定')
        self.assertFalse(self.execute('点击保存')['action_sent'])
        self.uia.read.side_effect = self.read
        self.uia.act.side_effect = RuntimeError('UIA操作已尝试，回执不确定')
        result = self.execute('点击保存')
        self.assertIsNone(result['action_sent'])
        self.assertEqual(self.uia.act.call_count, 1)
        self.obj.target_resolver.assert_not_called()
        self.obj._mouse_click.assert_not_called()

    def test_explicit_uia_no_input_fallback_clicks_exactly_once(self):
        self.uia.act.return_value = dict(fallback=True, input_sent=False, rect=self.items[0]['rect'])
        self.assertTrue(self.execute('点击保存')['ok'])
        self.assertEqual(self.uia.act.call_count, 1)
        self.obj._mouse_click.assert_called_once()

    def test_uia_ambiguous_fallback_receipt_never_clicks_again(self):
        self.uia.act.return_value = dict(fallback=True, rect=self.items[0]['rect'])
        result = self.execute('点击保存')
        self.assertIsNone(result['action_sent'])
        self.obj._mouse_click.assert_not_called()

    def test_empty_action_receipt_is_uncertain_not_success_or_retry(self):
        self.uia.act.return_value = {}
        result = self.execute('点击保存')
        self.assertFalse(result['ok'])
        self.assertIsNone(result['action_sent'])
        self.assertEqual(self.uia.act.call_count, 1)
        self.obj._mouse_click.assert_not_called()

    def test_list_bubble_contains_flags_not_values_and_is_not_silent(self):
        self.items += [element('disabled', '不可用', enabled=False), element('hidden', '隐藏', offscreen=True),
                       element('password', '密码输入', 'Edit', password=True, value='DO_NOT_EXPOSE')]
        result = self.execute('看看能点什么')
        self.assertTrue(result['ok'])
        self.assertFalse(result['silent'])
        self.assertFalse(result['action_sent'])
        self.assertIn('不可用', result['bubble'])
        self.assertIn('不在当前画面内', result['bubble'])
        self.assertIn('密码', result['bubble'])
        self.assertNotIn('DO_NOT_EXPOSE', str(result))
        self.assertNotIn('DO_NOT_EXPOSE', str(self.obj._options))
        self.no_actions()

    def test_numbered_click_rereads_ref_name_type_then_consumes_list(self):
        self.execute('列出可点选项')
        self.assertTrue(self.execute('点击第1项')['ok'])
        self.uia.read.assert_called_once_with(123, ref='save', name='保存', control_type='Button')
        self.assertIsNone(self.obj._options)
        self.assertFalse(self.execute('点击1号')['action_sent'])
        self.assertEqual(self.uia.act.call_count, 1)

    def test_expired_numbers_and_changed_identity_do_not_dispatch(self):
        self.execute('列出可点选项')
        self.obj._options['at'] -= 30
        self.assertFalse(self.execute('点击1号')['action_sent'])
        self.execute('列出可点选项')
        self.window['title'] = '另一文档'
        self.assertFalse(self.execute('点击1号')['action_sent'])
        self.no_actions()

    def test_numbered_item_change_in_position_name_type_is_rejected(self):
        for field, value in (('rect', dict(left=25, top=40, right=200, bottom=80)),
                             ('name', '另存为'), ('type', 'MenuItem')):
            with self.subTest(field=field):
                self.items[0] = element()
                self.execute('列出可点选项')
                changed = element(**{field: value})
                self.uia.read.side_effect = None
                self.uia.read.return_value = dict(element=changed)
                self.assertFalse(self.execute('点击1号')['action_sent'])
        self.no_actions()

    def test_disabled_number_cannot_be_clicked(self):
        self.items[0]['enabled'] = False
        self.execute('列出可点选项')
        self.assertFalse(self.execute('点击1号')['action_sent'])
        self.uia.read.assert_not_called()
        self.no_actions()

    def test_every_other_action_invalidates_numbers_even_on_uncertain_result(self):
        self.execute('列出可点选项')
        self.assertTrue(self.execute('按Tab')['ok'])
        self.assertIsNone(self.obj._options)
        self.execute('列出可点选项')
        self.uia.act.side_effect = TimeoutError('结果不明')
        self.execute('点击保存')
        self.assertIsNone(self.obj._options)

    def test_missing_visual_support_reports_reason(self):
        with patch.dict(sys.modules, {'core.desktop_targeting': None}):
            result = self.execute('点击不存在')
        self.assertFalse(result['action_sent'])
        self.assertIn('未接入视觉', result['error'])
        self.no_actions()

    def test_visual_target_validated_before_single_click_no_business_claim(self):
        self.obj.target_resolver = Mock(return_value=visual(name='图标'))
        self.obj.target_validator = Mock()
        result = self.execute('点击图标')
        self.assertTrue(result['ok'])
        self.assertFalse(result['verified'])
        self.assertEqual(result['source'], 'visual')
        self.assertIn('请看画面确认结果', result['message'])
        self.obj.target_resolver.assert_called_once_with(123, '图标')
        self.obj.target_validator.assert_called_once()
        self.obj._mouse_click.assert_called_once()
        self.uia.act.assert_not_called()

    def test_visual_bad_window_age_rect_or_validation_never_clicks(self):
        self.obj.target_validator = Mock()
        bad = [visual(window_fingerprint={}), visual(observed_at=time.monotonic() - 31),
               visual(rect=dict(left=-1, top=0, right=10, bottom=10)), visual(frame_digest='')]
        for candidate in bad:
            with self.subTest(candidate=candidate):
                self.obj.target_resolver = Mock(return_value=candidate)
                self.assertFalse(self.execute('点击图标')['action_sent'])
        self.obj.target_resolver = Mock(return_value=visual(name='图标'))
        self.obj.target_validator.side_effect = RuntimeError('画面已变化')
        self.assertFalse(self.execute('点击图标')['action_sent'])
        self.no_actions()

    def test_visual_validator_dynamic_import_and_foreground_recheck(self):
        candidate = visual(name='图标')
        self.obj.target_resolver = Mock(return_value=candidate)
        validator = Mock(side_effect=lambda hwnd, result: self.window.update(title='Changed'))
        with patch.dict(sys.modules, {'core.desktop_targeting': SimpleNamespace(validate_target=validator)}):
            self.assertFalse(self.execute('点击图标')['action_sent'])
        validator.assert_called_once()
        self.obj._mouse_click.assert_not_called()

    def test_visual_list_only_if_uia_has_no_controls_and_number_revalidates(self):
        self.items = []
        snapshot = visual(frame_signature=[1, 2, 3],
                          elements=[dict(name='图标', rect=dict(left=20, top=40, right=200, bottom=80))])
        self.obj.target_resolver = Mock(return_value=snapshot)
        self.obj.target_validator = Mock()
        result = self.execute('列出可点选项')
        self.assertEqual(result['source'], 'visual')
        self.obj.target_resolver.assert_called_once_with(123, '')
        self.assertTrue(self.execute('点一号')['ok'])
        self.obj.target_validator.assert_called_once()
        self.assertEqual(self.obj.target_validator.call_args.args[1]['frame_signature'], [1, 2, 3])
        self.obj._mouse_click.assert_called_once()
        self.uia.read.assert_not_called()
        self.assertIsNone(self.obj._options)

    def test_existing_uia_list_never_gets_visual_supplements(self):
        self.obj.target_resolver = Mock()
        self.assertTrue(self.execute('列出可点选项')['ok'])
        self.obj.target_resolver.assert_not_called()

    def test_visual_not_used_for_input_or_action_unsupported(self):
        self.obj.target_resolver = Mock()
        self.assertFalse(self.execute('在不存在框里输入你好')['action_sent'])
        self.assertFalse(self.execute('双击保存')['action_sent'])
        self.obj.target_resolver.assert_not_called()
        self.no_actions()

    def test_provider_unavailable_before_lookup_can_use_visual(self):
        self.uia.read.side_effect = UIAUnavailable('进程内 UIAutomation 不可用')
        self.obj.target_resolver = Mock(return_value=visual(name='保存'))
        self.obj.target_validator = Mock()
        self.assertTrue(self.execute('点击保存')['ok'])
        self.assertEqual(self.obj._mouse_click.call_count, 1)

    def test_busy_does_not_queue_and_lifecycle_reuses_service(self):
        self.obj.lock.acquire()
        try:
            self.assertEqual(self.execute('点击保存')['code'], 'desktop_busy')
            self.obj._guard.assert_not_called()
        finally:
            self.obj.lock.release()
        self.obj.warmup()
        self.uia.warmup.assert_called_once()
        self.obj.close()
        self.uia.close.assert_called_once()


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.gui = SimpleNamespace(GetForegroundWindow=Mock(return_value=123), GetClassName=Mock(return_value='Notepad'),
                                   GetDesktopWindow=Mock(return_value=1), GetWindowText=Mock(return_value='测试文档'),
                                   GetWindowRect=Mock(return_value=(0, 0, 1000, 800)))
        self.process = SimpleNamespace(name=Mock(return_value='notepad.exe'), create_time=Mock(return_value=42.0))
        self.pid = Mock(return_value=(99, 567))
        self.patches = patch.dict(sys.modules, {'win32gui': self.gui,
                                'win32process': SimpleNamespace(GetWindowThreadProcessId=self.pid),
                                'psutil': SimpleNamespace(Process=Mock(return_value=self.process))})
        self.patches.start()
        self.addCleanup(self.patches.stop)

    def test_fingerprint_contract_and_no_side_effect(self):
        result = window_fingerprint(123)
        self.assertEqual(set(result), {'hwnd', 'pid', 'process', 'rect', 'title'})
        self.assertEqual(result['rect'], [0, 0, 1000, 800])
        self.assertEqual(DesktopInteraction._guard(123), identity())

    def test_rejects_desktop_taskbar_own_pid_and_explicit_wrong_hwnd(self):
        for kind in ('Progman', 'WorkerW', 'Shell_TrayWnd', 'Shell_SecondaryTrayWnd', '#32769'):
            self.gui.GetClassName.return_value = kind
            with self.assertRaises(RuntimeError):
                DesktopInteraction._guard()
        self.gui.GetClassName.return_value = 'Notepad'
        self.pid.return_value = (99, os.getpid())
        with self.assertRaisesRegex(RuntimeError, '自身'):
            DesktopInteraction._guard()
        with self.assertRaisesRegex(RuntimeError, '前台'):
            DesktopInteraction._guard(999)

    def test_foreground_changes_during_guard(self):
        self.gui.GetForegroundWindow.side_effect = [123, 999]
        with self.assertRaisesRegex(RuntimeError, '前台'):
            DesktopInteraction._guard()


if __name__ == '__main__':
    unittest.main(verbosity=2)
