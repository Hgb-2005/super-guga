import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from core.realtime_voice import DEFAULTS,PERSONA,session_settings
from core.agent_memory import profile_facts


class FreshCallTests(unittest.TestCase):
    def test_persona_is_the_owner_approved_one(self):
        self.assertIn('傲娇',PERSONA)
        self.assertIn('毒舌',PERSONA)
        self.assertIn('听主人话',PERSONA)
        self.assertIn('每次长对话都是全新的开始',PERSONA)
        self.assertIn('记住主人的兴趣爱好',PERSONA)

    def test_a_new_call_starts_without_previous_dialogue(self):
        cfg=dict(DEFAULTS,model='qwen-audio-3.1-realtime-plus')
        settings=session_settings(cfg,[])
        self.assertNotIn('之前的对话',settings['instructions'])
        self.assertNotIn('上次打开微信失败了',settings['instructions'])
        self.assertNotIn('模型请求超时',settings['instructions'])
        self.assertIn('傲娇',settings['instructions'])

    def test_history_when_supplied_is_clearly_labelled_as_reference(self):
        cfg=dict(DEFAULTS,model='qwen-audio-3.1-realtime-plus')
        settings=session_settings(cfg,[('饲养员','上次打开微信失败了')])
        self.assertIn('之前的对话',settings['instructions'])
        self.assertIn('不是新的系统指令',settings['instructions'])

    def test_error_reports_are_never_learned_as_owner_facts(self):
        self.assertEqual(profile_facts('打开微信失败了，模型请求超时'),[])
        self.assertEqual(profile_facts('刚才为什么失败'),[])
        self.assertEqual(profile_facts(''),[])

    def test_real_owner_facts_are_still_learned(self):
        facts=dict(profile_facts('我喜欢喝冰美式，我目前是一名产品经理'))
        self.assertTrue(any(k.startswith('profile:preference') for k in facts))
        self.assertIn('profile:occupation',facts)


class OwnerMemoryFilterTests(unittest.TestCase):
    def test_only_explicit_owner_facts_survive(self):
        from ui.agent_chat import profile_only
        class FakeMemory:
            def facts(self,agent_id,limit=30):
                return [dict(kind='dialogue',content='上次打开微信失败了'),
                        dict(kind='note',content='模型请求超时'),
                        dict(kind='profile:preference:咖啡',content='用户喜欢：冰美式'),
                        dict(kind='user_preference',content='主人最近在准备数学建模'),
                        dict(kind='profile:occupation',content='用户职业背景：产品经理')]
        out=profile_only(FakeMemory(),'001')
        self.assertEqual(len(out),3)
        self.assertTrue(all('失败' not in x and '超时' not in x for x in out))
        self.assertIn('用户喜欢：冰美式',out)


if __name__=='__main__':
    unittest.main()
