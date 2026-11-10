# -*- coding: utf-8 -*-
"""离线单测：DSH 网关客户端的纯函数与安全边界（不联网）。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.super_agent.dsh_gateway import _no_secret, session_id_from_focused, session_title, DshGateway


class GatewayOfflineTests(unittest.TestCase):
    def test_secrets_are_redacted_in_errors(self):
        message = 'cookie rejected: dsh-auth-abc123=very-secret-value; token=xyz'
        redacted = _no_secret(message)
        self.assertIn('<redacted>', redacted)
        self.assertNotIn('very-secret-value', redacted)
        self.assertNotIn('xyz', redacted)
        self.assertNotIn('secret-token-xyz', _no_secret('GET /connect?token=secret-token-xyz&x=1'))

    def test_session_id_mapping_accepts_session_prefix_and_bare_uuid(self):
        items = [{'sessionId': 'session-abc-123'}, {'sessionId': 'plain-def'}]
        self.assertEqual(session_id_from_focused('session-abc-123', items), 'session-abc-123')
        self.assertEqual(session_id_from_focused('abc-123', items), 'session-abc-123')
        self.assertEqual(session_id_from_focused('def', items), 'plain-def')
        self.assertEqual(session_id_from_focused('', items), '')
        self.assertEqual(session_id_from_focused('missing', items), '')

    def test_session_title_reads_projections_values(self):
        item = {'projections': {'kind': 'sequenced', 'values': {'title': '咕嘎操控与进度汇报'}}}
        self.assertEqual(session_title(item), '咕嘎操控与进度汇报')
        self.assertEqual(session_title({}), '')

    def test_prompt_requires_explicit_target(self):
        gateway = DshGateway()
        with self.assertRaises(ValueError):
            gateway.prompt('', 'do something')
        with self.assertRaises(ValueError):
            gateway.prompt('session-1', '')

    def test_prompt_rejects_unknown_mode_and_oversized_text(self):
        gateway = DshGateway()
        with self.assertRaises(ValueError):
            gateway.prompt('session-1', 'hello', mode='steal')
        with self.assertRaises(ValueError):
            gateway.prompt('session-1', 'x' * 8001)


if __name__ == '__main__':
    unittest.main()
