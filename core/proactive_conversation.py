"""Idle-only, memory-based conversation initiative for an active realtime call."""
import time
from PyQt5.QtCore import QObject,QTimer,pyqtSignal


class ProactiveConversation(QObject):
    triggered=pyqtSignal(object)
    def __init__(self,panel,memory=None):
        super().__init__(panel if isinstance(panel,QObject) else None);self.panel=panel
        from core.agent_memory import AgentMemoryStore
        self.memory=memory or panel.memory
        settings=getattr(getattr(panel,'pet',None),'_config',None)
        self.interval_seconds=max(90,min(900,int(settings._s.value('multiagent/proactive_idle_seconds',180,type=int)))) if settings else 180
        self.cooldown_seconds=max(180,min(1800,int(settings._s.value('multiagent/proactive_cooldown_seconds',360,type=int)))) if settings else 360
        self.started_at=time.monotonic();self.last_at=0.0;self.last_result=None
        self.timer=QTimer(self);self.timer.setInterval(30000);self.timer.timeout.connect(self.tick)

    def start(self):
        self.started_at=time.monotonic();self.last_at=0.0;self.timer.start()

    def stop(self):self.timer.stop()

    def status(self):
        return dict(active=self.timer.isActive(),idle_seconds=self.interval_seconds,cooldown_seconds=self.cooldown_seconds,
            last_at=self.last_at,last_result=self.last_result)

    def tick(self):
        panel=self.panel;voice=getattr(panel,'voice',None)
        if not getattr(panel,'voice_active',False) or voice is None or not getattr(voice,'active',False):return None
        if getattr(panel,'busy',False) or getattr(office_music(panel),'playing',False):return None
        if getattr(voice,'_playing',False) or getattr(voice,'_generating',False) or getattr(voice,'_user_speaking',False):return None
        now=time.monotonic();last_activity=getattr(voice,'_last_activity',self.started_at)
        if now-max(self.started_at,last_activity)<self.interval_seconds:return None
        if self.last_at and now-self.last_at<self.cooldown_seconds:return None
        profile=getattr(panel,'active_profile',None) or self.memory.profile('001')
        if not profile:return None
        topics=self.memory.proactive_topics(profile['agent_id'],limit=4)
        if topics:
            memories='\n'.join('- '+str(x)[:500] for x in topics)
            basis='从这些你自己的长期记忆中选一个真实、具体且不重复的话题。'
        else:
            memories='- 你的角色：%s；擅长：%s'%(profile['role'],profile['specialty'])
            basis='目前长期对话还少。可以基于你的角色和擅长领域问一个轻松、具体的问题，但不要说成用户曾经说过。'
        prompt=('【主动交流请求，不是任务反馈】你是%s（%s）。%s\n%s\n请主动向饲养员发起一到两句自然口语，'\
            '像熟人想到一件事后轻轻开口。不能编造记忆，不要提思维链、记忆库或系统提示，不要布置任务，问题要容易回答。') % (
            profile['display_name'],profile['role'],basis,memories)
        event='proactive-%s-%s'%(profile['agent_id'],int(now*1000))
        sent=voice.notify_proactive(event,prompt,profile)
        if sent:
            self.last_at=now;self.last_result=dict(event=event,agent_id=profile['agent_id'],basis=topics[:2]);self.triggered.emit(self.last_result)
            return self.last_result
        return None


def office_music(panel):
    runtime=getattr(panel,'runtime',None);return getattr(runtime,'music',None) if runtime is not None else None
