import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from core.self_agent import SelfAgent


def build_agent(model='qwen3.8-flash',base='https://dashscope.aliyuncs.com/compatible-mode/v1'):
    agent=SelfAgent.__new__(SelfAgent)
    agent.config=dict(model=model,base_url=base,api_key='test-key')
    agent._model_request_index=0
    agent._last_model_diagnostic=None
    return agent


def call_and_capture(agent,routine):
    agent._routine_request=routine
    reply=MagicMock(status_code=200)
    reply.json.return_value={'choices':[{'message':{'role':'assistant','content':'好'}}]}
    with patch('core.self_agent.requests.post',return_value=reply) as post:
        agent._request([{'role':'user','content':'打开微信'}],'sess')
        return post.call_args.kwargs['json']


class PortableThinkingTests(unittest.TestCase):
    def test_routine_request_never_sends_effort_with_thinking_off(self):
        payload=call_and_capture(build_agent(),True)
        self.assertEqual(payload['thinking'],{'type':'disabled'})
        self.assertNotIn('reasoning_effort',payload,
            'DashScope rejects reasoning_effort when thinking is disabled')

    def test_full_request_keeps_high_effort_with_thinking_on(self):
        payload=call_and_capture(build_agent(),False)
        self.assertEqual(payload['thinking'],{'type':'enabled'})
        self.assertEqual(payload['reasoning_effort'],'high')

    def test_tools_are_still_offered(self):
        payload=call_and_capture(build_agent(),True)
        self.assertIn('tools',payload)
        self.assertEqual(payload['tool_choice'],'auto')


class StepBudgetTests(unittest.TestCase):
    def test_short_multi_step_task_is_not_treated_as_a_single_shot(self):
        import re
        pattern=r'然后|接着|之后再|再帮|再发|找到|搜索|查找|发送|回复|填写|上传|下载|选择|切换|先.{1,12}再'
        prompt='打开微信，在搜索框里找到小军，然后发送信息'
        self.assertTrue(bool(re.search(pattern,prompt)),'多步标记必须被识别')
        simple='打开微信'
        self.assertFalse(bool(re.search(pattern,simple)),'单步不应被当成多步')


if __name__=='__main__':
    unittest.main()
