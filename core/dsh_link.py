"""Read-only task facts from the independent native DSH host, isolated by session."""
import time
from collections import OrderedDict
from PyQt5.QtCore import QObject,QTimer


_ACTION_WORDS = (
    ('mcp__guga__applications', '帮你打开要用的软件'),
    ('mcp__guga__computer', '在电脑上帮你操作画面'),
    ('mcp__guga__pet_say', '跟你同步消息'),
    ('mcp__guga__pet_control', '摆弄一下我的小动作'),
    ('mcp__guga__app_state', '检查我这边的情况'),
    ('mcp__guga__wechat', '帮你发微信'),
    ('mcp__guga__daily_plan', '帮你整理每日计划'),
    ('mcp__guga__workspace', '帮你整理项目和会话'),
    ('mcp__guga__voice_session', '准备语音通话'),
    ('mcp__guga__screen_share', '帮你看着屏幕'),
)
_OPENERS = ('主人～', '跟你汇报下哈～', '进度来啦～', '我跟你讲哦～')
_SUMMARY_WORDS = (
    ('applications', '帮你操作软件'),
    ('computer', '帮你操作电脑画面'),
    ('screen_share', '帮你看屏幕'),
    ('pet_say', '跟你说话'),
    ('wechat', '帮你发微信'),
    ('app_state', '检查我这边状态'),
    ('desktop_command', '帮你拉起软件'),
    ('screen_context', '帮你看着屏幕'),
    ('lingshu_memory', '翻灵枢记忆'),
)
_TERMINAL_WORDS = {
    'finished': '主人～DSH 这一轮干完啦，我把结果整理给你～',
    'failed': '主人～DSH 那边任务失败了，我先看看是怎么回事，再跟你说～',
    'cancelled': '主人～DSH 这边已经停下了，我就不乱动啦～',
    'waiting': '主人～DSH 在等你补充信息呢，你有空瞄一眼哈～',
}


def _goal_hint(row):
    """从 DSH 目标里取一段真正说人话的任务提示（跳过运行时噪声）。"""
    goal = str(row.get('goal') or '').replace('\n', ' ').strip()
    if not goal or len(goal) > 90:
        return ''
    if goal.lower().startswith('current runtime context'):
        return ''
    return goal


def _plain_progress(row):
    """把 DSH 的结构化事实翻成咕嘎的大白话，去掉会话编号和日志腔。"""
    summary = str(row.get('summary') or '').strip()
    tool = str(row.get('tool') or '').strip()
    step = row.get('step')
    state = str(row.get('state') or '')
    if state in _TERMINAL_WORDS:
        return _TERMINAL_WORDS[state]
    hint = _goal_hint(row)
    tail = '，你说的「' + hint + '」我记着呢' if hint else ''
    marker = 'DSH 的公开答复：'
    if summary.startswith(marker):
        body = summary[len(marker):].replace('\n', ' ').strip()
        if body:
            return body[:160]
    last_answer = str(row.get('last_answer') or '').strip()
    probe = (tool + ' ' + summary).lower()
    for key, words in _SUMMARY_WORDS:
        if key in probe:
            if last_answer:
                return last_answer[:150]
            return _OPENERS[(step or 0) % len(_OPENERS)] + '我正在' + words + '呢，我盯着呢，有结果马上告诉你～' + tail
    if last_answer:
        return last_answer[:150]
    return '主人～我还在帮你干活呢，我会一直盯着，弄好了第一时间跟你说～' + tail


class DshLink(QObject):
    def __init__(self,panel,clock=None,interval=30):
        super().__init__(panel if isinstance(panel,QObject) else None)
        self.panel=panel;self.clock=clock or time.monotonic
        self.sessions=OrderedDict();self.focused_session=None
        self.interval=max(15,int(interval));self.last_notice=0;self.event_count=0
        self.host_seen=None
        self.timer=QTimer(self);self.timer.setInterval(1000);self.timer.timeout.connect(self.tick);self.timer.start()

    def receive(self,args):
        from core.desktop_access import redact
        session=str(args.get('session_id','')).strip()
        if not session or len(session)>160:raise ValueError('DSH 会话编号无效')
        if args.get('kind')=='host/heartbeat':
            self.host_seen=self.clock();return dict(ok=True,connected=True)
        state=str(args.get('state',''))
        if state not in ('','running','finished','failed','cancelled','waiting'):raise ValueError('DSH 任务状态无效')
        now=self.clock();old=self.sessions.get(session,{})
        self.host_seen=now
        turn=args.get('turn')
        if turn is not None and old.get('turn') is not None and int(turn)<int(old['turn']):
            return dict(ok=True,stale_event_ignored=True)
        value=dict(old,session_id=session,updated_at=now,kind=str(args.get('kind',''))[:40])
        if value['kind']!='heartbeat':value['last_event_at']=now
        for key,limit in (('goal',500),('summary',500),('tool',100),('reason',100),('event_id',100),('parent_session_id',160)):
            if key in args:value[key]=redact(str(args[key]))[:limit]
        answer=str(value.get('summary') or '')
        marker='DSH 的公开答复：'
        if answer.startswith(marker):
            body=answer[len(marker):].replace('\n',' ').strip()
            if body:value['last_answer']=body[:300]
        for key in ('turn','step'):
            if key in args and args[key] is not None:value[key]=max(0,int(args[key]))
        if state:value['state']=state
        if old.get('event_id')==value.get('event_id') and old:return dict(ok=True,duplicate=True)
        value.setdefault('state','running')
        self.sessions[session]=value;self.sessions.move_to_end(session)
        while len(self.sessions)>16:self.sessions.popitem(last=False)
        if self.focused_session is None or (value['kind']=='user/message' and not value.get('parent_session_id')):self.focused_session=session
        self.event_count+=1
        if self.focused_session==session:
            voice=getattr(self.panel,'voice',None)
            if voice is not None and not getattr(self.panel,'busy',False):
                voice.set_task_context(dict(running=value['state']=='running',kind='external_dsh',goal=value.get('goal',''),summary=value.get('summary','')))
        if value.get('state') in ('finished','failed','cancelled','waiting') and old.get('state')!=value.get('state'):
            value['terminal_pending']=True
        try:
            if self.focused_session==session:
                from core.super_agent.screen_context import start_watching,stop_watching
                if value.get('state')=='running':start_watching('dsh-task')
                else:stop_watching()
        except Exception:
            pass
        return dict(ok=True,received=True,session_id=session,state=value['state'])

    def status(self):
        now=self.clock()
        rows=[dict(v,connected=now-v['updated_at']<45,age_seconds=round(now-v['updated_at'],1)) for v in self.sessions.values()]
        return dict(connected=self.host_seen is not None and now-self.host_seen<45,focused_session=self.focused_session,
                    focus_basis='most_recent_user_message',sessions=rows,events_received=self.event_count,report_interval_seconds=self.interval)

    def execute(self,args):
        if args.get('action','status')=='focus':
            session=str(args.get('session_id',''))
            if session not in self.sessions:raise ValueError('没有收到这个 DSH 会话的状态，请用真实会话编号')
            self.focused_session=session
        elif args.get('action','status')!='status':raise ValueError('外部 DSH 目前支持查询与关注会话；暂停、停止请在 DSH 内执行')
        return dict(ok=True,**self.status())

    def active_context(self):
        row=self.sessions.get(self.focused_session)
        if not row or row.get('state')!='running' or self.clock()-row['updated_at']>=45:return None
        return dict(running=True,kind='external_dsh',goal=row.get('goal',''),summary=row.get('summary',''))

    def tick(self):
        row=self.sessions.get(self.focused_session)
        if not row:return
        now=self.clock()
        try:
            from core.super_agent.screen_context import get_watcher
            watcher=get_watcher()
            if watcher.active and now-row.get('updated_at',now)>180:watcher.stop()
        except Exception:
            pass
        if not row.get('terminal_pending') and (row.get('state')!='running' or now-self.last_notice<self.interval):return
        panel=self.panel
        allowed=getattr(panel,'companion_chat_allowed',None)
        if callable(allowed) and not allowed():return
        if not callable(allowed) and getattr(panel,'busy',False):return
        if getattr(panel,'dialogue',None) is not None and panel.dialogue.active:return
        if now-row['updated_at']>=45:
            if row.get('stale_notified'):return
            text='主人，我这边暂时听不到 DSH 的动静了，还不能判断任务是不是结束了，我先不乱动，等它回来～'
            row['stale_notified']=True
            voice=getattr(panel,'voice',None)
            if voice and not getattr(panel,'busy',False):
                voice.set_task_context(dict(running=False,kind='external_dsh_unknown',goal=row.get('goal',''),summary='进度连接暂时中断，无法确定任务是否仍在执行；没有发出停止指令'))
        else:
            row.pop('stale_notified',None)
            text=_plain_progress(row)
            try:
                from core.super_agent.narration import compose
                text=compose(text,with_memory=True)
            except Exception:
                pass
        voice=getattr(panel,'voice',None)
        if getattr(panel,'voice_active',False) and voice:
            if not voice.notify_battleground('dsh-link-'+row.get('event_id',''),text,during_task=True):return
        else:
            if hasattr(panel,'_show_active_bubble'):panel._show_active_bubble(text)
            panel._say_active_reply(text,'happy','none',speak=True,conversation_id=panel.active_id)
        row['terminal_pending']=False;row['last_spoken_goal']=row.get('goal');self.last_notice=now
        try:
            from core.super_agent.narration import log_report
            log_report(text,str(row.get('state') or ''))
        except Exception:
            pass
        try:
            from core.super_agent.screen_context import stop_watching
            if row.get('state') in ('finished','failed','cancelled','waiting'):
                stop_watching()
                if row.get('state')=='finished':
                    from core.super_agent.lingshu_memory import remember_async
                    remember_async('DSH 任务进度回顾',text)
        except Exception:
            pass


def format_status(state):
    rows=state.get('sessions',[])
    if not rows:return '还没有收到本地 DSH 的任务回执。重启本机 DSH，确认“咕嘎 · 桌面助手”插件启用，再在 DSH 中发起任务。'
    labels=dict(running='进行中',finished='本轮结束',failed='失败',cancelled='已取消',waiting='等待')
    return '\n'.join(('★ ' if x['session_id']==state.get('focused_session') else '')+x['session_id']+' · '+labels.get(x.get('state'),x.get('state','未知'))+
        ('（进度连接已中断）' if not x.get('connected') and x.get('state')=='running' else '')+'\n目标：'+x.get('goal','未取得')+'\n'+x.get('summary','暂无进度') for x in rows)
