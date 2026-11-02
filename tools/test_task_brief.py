import unittest

from core.task_brief import DshTaskBrief


class TaskBriefTests(unittest.TestCase):
    def test_status_answers_from_dsh_events_without_another_model(self):
        brief = DshTaskBrief('j1', '把最新会议录制上传到B站', clock=lambda: 100)
        brief.observe(dict(type='status', phase='step_start'))
        brief.observe(dict(type='tool_receipt', receipt=dict(name='computer',
            arguments=dict(action='click'), result=dict(ok=True))))
        answer = brief.answer('现在做到哪了，为什么还没完成？')
        self.assertIn('最新会议录制', answer)
        self.assertIn('操作桌面界面', answer)
        self.assertIn('尚未', answer)
        self.assertIn('DSH', answer)

    def test_watchdog_reports_every_minute_even_when_receipts_stop(self):
        now = [0]
        brief = DshTaskBrief('j2', '整理视频', clock=lambda: now[0])
        self.assertIsNone(brief.watchdog(now=59))
        first = brief.watchdog(now=60)
        now[0] = 120
        second = brief.watchdog(now=120)
        self.assertIn('第 1 分钟', first)
        self.assertIn('第 2 分钟', second)
        self.assertIn('没有收到', first)
        self.assertIn('没有收到', second)
        self.assertIn('原因还不能确认', second)
        self.assertIsNone(brief.watchdog(now=121))

    def test_failure_reason_is_factual_and_bounded(self):
        brief = DshTaskBrief('j3', '打开会议', clock=lambda: 0)
        brief.observe(dict(type='tool_receipt', receipt=dict(name='computer',
            arguments=dict(action='click'), result=dict(ok=False))))
        brief.observe(dict(type='report', state='blocked', summary='目标窗口最小化',
            blockers=['窗口无法恢复']))
        answer = brief.answer('为什么卡住了？')
        self.assertIn('目标窗口最小化', answer)
        self.assertIn('窗口无法恢复', answer)
        self.assertIn('任务尚未完成', answer)

    def test_needs_input_stops_repetitive_progress_and_is_not_success(self):
        brief = DshTaskBrief('j4', '上传视频', clock=lambda: 0)
        brief.observe(dict(type='report', state='needs_input', summary='请确认公开范围'))
        self.assertIsNone(brief.watchdog(now=300))
        self.assertIn('需要你决定', brief.answer('现在什么情况'))
        brief.complete(False, 'needs_input', '')
        self.assertFalse(brief.active)
        self.assertIn('等待你的决定', brief.phase)


if __name__ == '__main__':
    unittest.main()
