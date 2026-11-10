# -*- coding: utf-8 -*-
"""离线单测：咕嘎内部智能体的工具表包含超级智能体工具，且 schema 形状合法。"""
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.self_agent import TOOLS, SelfAgent  # noqa: E402

SUPER_AGENT_TOOLS = ('dsh_gateway', 'screen_context', 'desktop_command', 'lingshu_memory')


class FakeBridge:
    def call(self, *args, **kwargs):
        return {'ok': True, 'result': {}}


class SuperAgentToolSchemaTests(unittest.TestCase):
    def test_super_agent_tools_are_visible_to_internal_agent(self):
        names = {item['function']['name'] for item in TOOLS}
        for name in SUPER_AGENT_TOOLS:
            self.assertIn(name, names, '内部智能体缺少工具：' + name)

    def test_schema_shapes_are_valid(self):
        by_name = {item['function']['name']: item['function'] for item in TOOLS}
        for name in SUPER_AGENT_TOOLS:
            schema = by_name[name]
            self.assertTrue(schema.get('description'))
            params = schema.get('parameters') or {}
            self.assertEqual(params.get('type'), 'object')
            self.assertIn('action', params.get('properties') or {},
                          name + ' 缺少 action 参数')
            self.assertIn('action', params.get('required') or [],
                          name + ' 必须把 action 标为必填')

    def test_gateway_prompt_is_explicitly_a_write_action(self):
        by_name = {item['function']['name']: item['function'] for item in TOOLS}
        description = by_name['dsh_gateway']['description']
        self.assertIn('session_id', description)
        self.assertTrue('写操作' in description or '不能猜' in description)

    def test_agent_request_payload_carries_super_agent_tools(self):
        """抓内部智能体真正发给模型的 JSON（mock requests.post），证明新工具进了可调用列表。"""
        agent = SelfAgent(FakeBridge(), config={'api_key': 'test'})
        captured = {}

        class FakeResponse:
            status_code = 200

            def json(self):
                return {'choices': [{'message': {'content': '好的', 'tool_calls': []}}]}

            def close(self):
                pass

        def fake_post(*args, **kwargs):
            payload = kwargs.get('json') or {}
            captured['names'] = [item['function']['name'] for item in (payload.get('tools') or [])]
            return FakeResponse()

        with patch('core.self_agent.requests.post', side_effect=fake_post):
            agent._run('payload-probe', [], '看看屏幕上是什么', threading.Event())
        for name in SUPER_AGENT_TOOLS:
            self.assertIn(name, captured.get('names') or [],
                          '模型 payload 里缺少工具：' + name)


if __name__ == '__main__':
    unittest.main()
