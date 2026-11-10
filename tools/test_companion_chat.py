"""Chat entrypoint checks with isolated Qt/workspace and intercepted model calls."""
import unittest
from unittest.mock import patch
from tools import test_personal_harness as harness


class CompanionChatTests(unittest.TestCase):
    setUpClass=harness.IntegrationTests.__dict__['setUpClass']
    tearDownClass=harness.IntegrationTests.__dict__['tearDownClass']
    setUp=harness.IntegrationTests.setUp

    def test_natural_routine_saved_in_chat_and_available_in_another_chat_payload(self):
        p=self.panel
        with patch.object(p.agent,'start') as agent,patch.object(p.runtime.dsh,'start') as dsh:
            p.send('我的每日计划是：早上学习英语；晚上跑步半小时')
            agent.assert_not_called();dsh.assert_not_called()
        self.assertIn('记住',p.store.messages(p.active_id)[-1]['content'])
        p.open_conversation(p.store.create_conversation())
        with patch.object(p.answer,'start') as answer:
            p.send('今天心情不错，聊聊天')
            self.assertIn('晚上跑步半小时',answer.call_args.kwargs['memory_context'])
        p.busy=False

    def test_remember_prefix_does_not_create_a_second_copy_of_the_plan(self):
        p=self.panel
        p.send('记住：我的每日计划是：睡前阅读')
        self.assertTrue(any(x['title']=='睡前阅读' for x in p.runtime.daily_plan.snapshot()['templates']))
        self.assertFalse(any('睡前阅读' in x['content'] for x in p.memory.facts('001')))

    def test_project_and_conversation_memories_stay_in_their_scope(self):
        p=self.panel
        project=p.store.create_project('验收项目')
        a=p.store.create_conversation(project_id=project)
        b=p.store.create_conversation(project_id=project)
        other=p.store.create_conversation()
        p.open_conversation(a)
        p.send('记住：这个项目：主题是海边旅行')
        p.send('记住：本对话：今天有点紧张')
        self.assertIn('紧张',p._memory_context(''))
        p.open_conversation(b)
        self.assertIn('海边旅行',p._memory_context(''))
        self.assertNotIn('紧张',p._memory_context(''))
        p.open_conversation(other)
        self.assertNotIn('海边旅行',p._memory_context(''))


if __name__=='__main__':unittest.main()
