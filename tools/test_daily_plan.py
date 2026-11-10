"""Checklist behavior, historical accuracy, chat parsing and companion evidence."""
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
import tempfile
import sys
import unittest
from unittest.mock import MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.daily_plan import DailyPlan, parse_command, format_plan
from launcher import _prepare_qt
_prepare_qt()
from PyQt5.QtWidgets import QApplication
APP=QApplication.instance() or QApplication([])


class DailyPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.day=date(2026,11,9)
        self.path=Path(self.tmp.name)/'plan.sqlite3'
        self.plan=DailyPlan(self.path,today=lambda:self.day)

    def tearDown(self):self.tmp.cleanup()

    def add(self,title='学习',**kwargs):return self.plan.execute(dict(action='add',title=title,**kwargs))

    def test_empty_and_chat_examples(self):
        self.assertEqual(self.plan.snapshot()['total'],0)
        self.assertIn('设置每日计划',format_plan(self.plan.snapshot()))
        self.assertEqual(parse_command('设置每日计划：学习；运动')['titles'],['学习','运动'])
        self.assertEqual(parse_command('每日计划：学习\n运动')['titles'],['学习','运动'])
        self.assertIsNone(parse_command('解释如何设置每日计划'))
        self.assertIsNone(parse_command('我没有完成每日计划'))

    def test_check_uncheck_and_restart(self):
        self.add()
        self.plan.execute(dict(action='check',title='学习'))
        self.assertEqual(DailyPlan(self.path,today=lambda:self.day).snapshot()['completed'],1)
        self.plan.execute(dict(action='check',title='学习'))
        self.assertEqual(self.plan.snapshot()['completed'],1)
        self.plan.execute(dict(action='uncheck',title='学习'))
        self.assertEqual(self.plan.snapshot()['completed'],0)

    def test_next_day_starts_unchecked_and_history_survives(self):
        self.add();self.plan.execute(dict(action='check',title='学习'))
        old=self.day;self.day+=timedelta(days=1)
        self.assertEqual(self.plan.snapshot()['completed'],0)
        self.assertEqual(self.plan.snapshot(old)['completed'],1)

    def test_weekdays_do_not_appear_on_rest_day(self):
        self.add(weekdays=[1,3,5])
        self.assertEqual(self.plan.snapshot()['total'],1)
        self.day+=timedelta(days=1)
        self.assertEqual(self.plan.snapshot()['total'],0)
        with self.assertRaisesRegex(ValueError,'今天不重复'):
            self.plan.execute(dict(action='check',title='学习'))

    def test_rename_and_delete_keep_unvisited_past(self):
        self.add()
        original=self.day
        self.day+=timedelta(days=2)
        self.plan.execute(dict(action='update',title='学习',new_title='阅读',weekdays=[3]))
        self.assertEqual(self.plan.snapshot(original+timedelta(days=1))['items'][0]['title'],'学习')
        self.plan.execute(dict(action='delete',title='阅读'))
        self.assertEqual(self.plan.snapshot()['total'],0)
        self.assertEqual(self.plan.snapshot(original)['items'][0]['title'],'学习')

    def test_new_tasks_never_backfill_previous_day(self):
        old=self.day;self.day+=timedelta(days=1);self.add()
        self.assertEqual(self.plan.snapshot(old)['total'],0)

    def test_today_rename_keeps_check(self):
        self.add();self.plan.execute(dict(action='check',title='学习'))
        self.plan.execute(dict(action='update',title='学习',new_title='阅读'))
        state=self.plan.snapshot()
        self.assertEqual((state['items'][0]['title'],state['completed']),('阅读',1))

    def test_duplicates_and_invalid_weekdays_do_not_modify_plan(self):
        self.add()
        for args in (dict(action='add',title='学习'),dict(action='add',title='运动',weekdays=[]),
                     dict(action='update',title='学习',weekdays=[True])):
            with self.assertRaises(ValueError):self.plan.execute(args)
        self.assertEqual(self.plan.snapshot()['total'],1)

    def test_past_and_future_cannot_be_checked(self):
        self.add()
        with self.assertRaises(ValueError):self.plan.execute(dict(action='check',title='学习',day='2026-11-08'))
        with self.assertRaises(ValueError):self.plan.snapshot('2026-11-10')

    def test_history_fills_missing_days_using_original_schedule(self):
        self.add();self.day+=timedelta(days=3)
        result=self.plan.execute(dict(action='history',days=7))
        self.assertEqual(len(result['history']),4)
        self.assertTrue(all(x['total']==1 for x in result['history']))

    def test_encouragement_only_lists_checked_titles(self):
        self.add();self.add('运动')
        self.assertIn('没有勾选',self.plan.encouragement())
        self.plan.execute(dict(action='check',title='学习'))
        line=self.plan.encouragement()
        self.assertIn('学习',line);self.assertNotIn('运动',line)
        self.assertIn('1/2',line)
        self.assertIn('未勾选：["运动"]',self.plan.reminder())

    def test_fast_route_and_mcp_schema_expose_daily_plan(self):
        from core.fast_commands import match
        from core.mcp_contract import self_tools,dsh_tools
        self.assertEqual(match('每日计划 完成 学习')['arguments']['action'],'check')
        for definitions in (self_tools(),dsh_tools()):
            names=[x['name'] for x in definitions]
            self.assertTrue({'computer','wechat','pet_say','daily_plan'}<=set(names))
            self.assertEqual(len(names),len(set(names)))

    def test_proactive_prompt_has_real_checks_and_offline_needs_no_model(self):
        from core.proactive_conversation import ProactiveConversation
        self.add();self.plan.execute(dict(action='check',title='学习'))
        panel=SimpleNamespace(memory=MagicMock(),pet=None,runtime=SimpleNamespace(daily_plan=self.plan))
        proactive=ProactiveConversation(panel,panel.memory)
        prompt=proactive._prompt({'display_name':'咕嘎'},[],style=4)
        self.assertIn('完成 1/1',prompt)
        output=[];proactive.offline_ready.connect(lambda event,text:output.append(text))
        proactive._compose_offline('daily','unused',style=4)
        self.assertIn('全部勾上',output[0]);proactive.stop()

    def test_dialog_shows_and_persists_checks(self):
        from ui.daily_plan_dialog import DailyPlanDialog
        from PyQt5.QtWidgets import QDialog
        from PyQt5.QtCore import Qt
        self.add()
        panel=QDialog()
        panel.runtime=SimpleNamespace(daily_plan=self.plan,execute=lambda name,args:self.plan.execute(args))
        dialog=DailyPlanDialog(panel)
        dialog.list.item(0).setCheckState(Qt.Checked)
        self.assertEqual(self.plan.snapshot()['completed'],1)
        self.assertIn('1/1',dialog.heading.text())
        dialog.close();panel.close()

    def test_enter_adds_once_without_empty_second_submit(self):
        from ui.daily_plan_dialog import DailyPlanDialog
        from PyQt5.QtWidgets import QDialog
        from PyQt5.QtTest import QTest
        from PyQt5.QtCore import Qt
        panel=QDialog()
        panel.runtime=SimpleNamespace(daily_plan=self.plan,execute=lambda name,args:self.plan.execute(args))
        dialog=DailyPlanDialog(panel)
        dialog.show()
        dialog.title_input.setText('阅读')
        QTest.keyClick(dialog.title_input,Qt.Key_Return)
        self.assertEqual(self.plan.snapshot()['total'],1)
        self.assertIn('已添加',dialog.feedback.text())
        dialog.close();panel.close()


if __name__=='__main__':unittest.main()
