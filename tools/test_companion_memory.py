"""Companion memory acceptance using isolated databases, no live user data."""
import sqlite3
import tempfile
import unittest
from datetime import date,timedelta
from pathlib import Path
from types import SimpleNamespace
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.agent_memory import AgentMemoryStore
from core.chat_workspace import ChatWorkspace
from core.daily_plan import DailyPlan,parse_command
from core.companion_memory import CompanionMemory


class CompanionMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.day=date(2026,11,10)
        self.memory=AgentMemoryStore(self.root/'memory.db')
        self.workspace=ChatWorkspace(self.root/'workspace.db')
        self.plan=DailyPlan(self.root/'plan.db',today=lambda:self.day)
        self.service=CompanionMemory(self.memory,self.workspace,self.plan)
        self.project=self.workspace.create_project('生活')
        self.a=self.workspace.create_conversation('安排',self.project)
        self.b=self.workspace.create_conversation('闲聊',self.project)
        self.c=self.workspace.create_conversation('其他项目')

    def tearDown(self):self.tmp.cleanup()

    def save(self,content,scope='conversation',chat=None):
        return self.service.execute('001',chat or self.a,dict(action='remember',content=content,scope=scope))

    def test_daily_routine_is_learned_once_and_survives_restart_and_new_chat(self):
        text='我的每日计划是：早上八点学习英语；晚上跑步半小时'
        args=parse_command(text);self.assertEqual(args['action'],'add_many')
        self.plan.execute(args);self.plan.execute(args)
        self.assertEqual(len(self.plan.snapshot()['templates']),2)
        reopened=CompanionMemory(AgentMemoryStore(self.root/'memory.db'),ChatWorkspace(self.root/'workspace.db'),
                                 DailyPlan(self.root/'plan.db',today=lambda:self.day))
        self.assertIn('晚上跑步半小时',reopened.context('001','今天聊什么',self.c))
        self.assertEqual(reopened.daily_plan.snapshot()['completed'],0)

    def test_plan_edit_and_delete_never_leave_stale_general_facts(self):
        self.plan.execute(parse_command('我每天要：学习；运动'))
        self.plan.execute(dict(action='update',title='学习',new_title='阅读'))
        self.plan.execute(dict(action='delete',title='运动'))
        context=self.service.context('001','计划',self.b)
        self.assertIn('阅读',context);self.assertNotIn('学习',context);self.assertNotIn('运动',context)
        self.assertEqual(self.memory.facts('001'),[])

    def test_scoped_facts_and_raw_dialogue_cannot_cross_conversations(self):
        self.save('长期喜欢咖啡','user');self.save('项目仅用蓝色','project');self.save('今天心情低落')
        self.service.record('001','user','刚才微信发送失败',self.a)
        same=self.service.context('001','',self.a)
        sibling=self.service.context('001','',self.b)
        unrelated=self.service.context('001','',self.c)
        self.assertIn('心情低落',same);self.assertNotIn('心情低落',sibling)
        self.assertIn('项目仅用蓝色',sibling);self.assertNotIn('项目仅用蓝色',unrelated)
        self.assertIn('喜欢咖啡',unrelated);self.assertNotIn('微信发送失败',unrelated)
        self.assertEqual(self.memory.recent_dialogue('001',conversation_id=self.b),[])
        self.assertEqual(self.memory.recall('001','微信',include_dialogue=True,conversation_id=self.b),
                         self.memory.recall('001','微信',conversation_id=self.b))

    def test_profile_question_in_another_chat_cannot_interpret_short_answer(self):
        self.service.record('001','assistant','你喜欢什么？',self.a)
        self.service.record('001','user','打不开',self.b)
        self.assertEqual(self.memory.facts('001'),[])
        self.service.record('001','user','我喜欢喝咖啡',self.a)
        self.assertIn('咖啡',self.service.context('001','随便聊聊',self.b))

    def test_corrections_and_forgetting_apply_to_the_next_recall(self):
        self.service.record('001','user','我是一名教师',self.a)
        self.service.record('001','user','我目前是一名设计师',self.b)
        context=self.service.context('001','工作',self.c)
        self.assertIn('设计师',context);self.assertNotIn('教师',context)
        facts=self.service.execute('001',self.c,dict(action='list'))['facts']
        ident=facts[0]['id']
        self.service.execute('001',self.c,dict(action='update',id=ident,content='用户职业背景：产品经理'))
        self.assertIn('产品经理',self.service.context('001','工作',self.b))
        self.service.execute('001',self.c,dict(action='forget',id=ident))
        self.assertNotIn('产品经理',self.service.context('001','工作',self.b))

    def test_other_project_and_other_member_cannot_delete_or_update_a_fact(self):
        self.save('项目保密配色','project')
        ident=self.memory.facts('001',project_id=self.project)[0]['id']
        for owner,chat in [('001',self.c),('002',self.a)]:
            for action in ('forget','update'):
                with self.assertRaises(ValueError):
                    self.service.execute(owner,chat,dict(action=action,id=ident,content='篡改'))
        self.assertEqual(self.memory.facts('001',project_id=self.project)[0]['content'],'项目保密配色')

    def test_explicit_remember_and_automatic_learning_use_the_same_profile_slots(self):
        self.save('我喜欢喝咖啡','user')
        self.service.record('001','user','我不喜欢喝咖啡',self.b)
        facts=self.memory.facts('001')
        self.assertEqual(len(facts),1)
        self.assertIn('不喜欢',facts[0]['content'])
        self.assertIn('记录于',self.service.context('001','咖啡',self.c))

    def test_weekly_and_complex_edits_go_to_plan_capable_agent_without_daily_guessing(self):
        from core.execution_contract import requires_execution
        for text in ['我的每日计划是：每周一三五跑步','我每周一学习英语','把每日计划里的跑步改成阅读']:
            self.assertIsNone(parse_command(text))
            self.assertTrue(requires_execution(text))

    def test_sensitive_and_non_declarative_daily_text_is_not_auto_saved(self):
        for text in ['如果我每天跑步，你会鼓励我吗？','我今天计划：学习；跑步','他说我每天跑步',
                     '我每天跑步怎么样','我每天不想运动','我的每日计划：学习；不要保存',
                     '我每天输入密钥 sk-'+'A'*32,'我每天学习，明天再做','我每天学习改成阅读']:
            with self.subTest(text=text):self.assertIsNone(parse_command(text))
        self.service.record('001','user','我喜欢咖啡，不要记住',self.a)
        self.assertEqual(self.memory.facts('001'),[])

    def test_batch_validation_is_atomic_and_keeps_checks(self):
        self.plan.execute(parse_command('我每天要：学习；跑步'))
        self.plan.execute(dict(action='check',title='学习'))
        with self.assertRaises(ValueError):self.plan.execute(dict(action='add_many',titles=['阅读','']))
        self.assertEqual(len(self.plan.snapshot()['templates']),2)
        self.plan.execute(parse_command('我每天要：学习；跑步'))
        self.assertEqual(self.plan.snapshot()['completed'],1)
        self.day+=timedelta(days=1)
        self.assertEqual(self.plan.snapshot()['completed'],0)

    def test_dsh_and_chat_share_scoped_memory_and_the_same_plan(self):
        from core.dsh_tools import DshTools
        import threading
        self.save('只在这个项目使用深蓝色','project')
        self.plan.execute(parse_command('我每天要：阅读'))
        manager=SimpleNamespace(stop_event=threading.Event(),state=dict(agent_id='001',conversation_id=self.b,ticket_id='t',goal='聊天'))
        panel=SimpleNamespace(memory=self.memory,agent=None,runtime=SimpleNamespace(companion_memory=self.service))
        tools=DshTools(panel,manager)
        result=tools.execute('personal_memory',dict(action='recall',query='深蓝色'))
        self.assertTrue(any('深蓝色' in x['content'] for x in result['memories']))
        self.assertEqual(result['daily_plan']['templates'][0]['title'],'阅读')
        manager.state['conversation_id']=self.c
        self.assertEqual(tools.execute('personal_memory',dict(action='recall',query='深蓝色'))['memories'],[])

    def test_legacy_rows_are_preserved_but_old_chats_are_not_guessed(self):
        with self.memory.connect() as db:
            db.execute("INSERT INTO agent_dialogue(agent_id,role,content,created) VALUES('001','user','旧错误原文',1)")
            db.execute("INSERT INTO agent_facts(agent_id,kind,content,created) VALUES('001','profile:occupation','用户职业背景：教师',1)")
        reopened=AgentMemoryStore(self.root/'memory.db')
        self.assertEqual(reopened.recent_dialogue('001',conversation_id=self.a),[])
        self.assertEqual(reopened.facts('001')[0]['scope'],'user')
        with reopened.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM agent_dialogue').fetchone()[0],1)


if __name__=='__main__':unittest.main()
