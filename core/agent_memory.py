"""Per-agent local memory. Credentials are redacted; no screenshot payloads."""
import re,sqlite3,time,json
from core.agent_identity import PERSONALITIES,VOICES
from contextlib import contextmanager
from pathlib import Path

from core.desktop_access import redact

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_PATH=ROOT/'runtime/multiagent/memory.sqlite3'
PROFILES={
    '001':dict(display_name='咕嘎',role='总执行官',specialty='综合分析、通用任务、电脑操作与项目监督',expression='happy',appearance='guga'),
    '002':dict(display_name='大肥鱼',role='技术审查员',specialty='DeepSeek Harness、方案审查、长任务拆解、测试验收与漏洞检查',expression='surprised',appearance='dafish'),
    '003':dict(display_name='Dora',role='橘子创意搭档',specialty='创意陪伴、音乐、橘子、生活话题与用户体验；同咕嘎一样可执行全部任务',expression='happy',appearance='dora'),
}
SECRET_PATTERNS=(re.compile(r'\b(?:oc_sk_[A-Za-z0-9_-]{16,}|sk-[A-Za-z0-9_-]{20,})\b'),)


def safe_text(value):
    text=redact(str(value or ''))[:12000]
    for pattern in SECRET_PATTERNS:text=pattern.sub('[REDACTED]',text)
    return text.strip()


def _tokens(text):
    clean=''.join(ch.lower() if ch.isalnum() or '\u4e00'<=ch<='\u9fff' else ' ' for ch in str(text))
    chinese=''.join(re.findall(r'[\u4e00-\u9fff]',clean))
    parts=set(re.findall(r'[a-z0-9]{2,}',clean))
    if chinese:
        parts.update(chinese[i:i+2] for i in range(max(0,len(chinese)-1)))
    return parts


class AgentMemoryStore:
    def __init__(self,path=None):
        self.path=Path(path or DEFAULT_PATH);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS agent_profiles (
                    agent_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, role TEXT NOT NULL,
                    specialty TEXT NOT NULL, expression TEXT NOT NULL, appearance TEXT NOT NULL DEFAULT 'guga', updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_dialogue (
                    id INTEGER PRIMARY KEY, agent_id TEXT NOT NULL, role TEXT NOT NULL,
                    content TEXT NOT NULL, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS agent_dialogue_agent_created
                    ON agent_dialogue(agent_id,created DESC);
                CREATE TABLE IF NOT EXISTS agent_facts (
                    id INTEGER PRIMARY KEY, agent_id TEXT NOT NULL, kind TEXT NOT NULL,
                    content TEXT NOT NULL, importance INTEGER NOT NULL DEFAULT 2,
                    created REAL NOT NULL, last_used REAL, uses INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS agent_facts_agent_importance
                    ON agent_facts(agent_id,importance DESC,created DESC);
                CREATE TABLE IF NOT EXISTS shared_dialogue (
                    id INTEGER PRIMARY KEY, speaker TEXT NOT NULL, role TEXT NOT NULL,
                    content TEXT NOT NULL, created REAL NOT NULL);
            ''')
            columns={row[1] for row in db.execute('PRAGMA table_info(agent_profiles)')}
            if 'appearance' not in columns:db.execute("ALTER TABLE agent_profiles ADD COLUMN appearance TEXT NOT NULL DEFAULT 'guga'")
            for column,definition in (('personality',"TEXT NOT NULL DEFAULT ''"),('voice_settings',"TEXT NOT NULL DEFAULT '{}'")):
                if column not in columns:db.execute('ALTER TABLE agent_profiles ADD COLUMN '+column+' '+definition)
            for agent_id,profile in PROFILES.items():
                existing=db.execute('SELECT * FROM agent_profiles WHERE agent_id=?',(agent_id,)).fetchone()
                if existing is None:
                    db.execute('INSERT INTO agent_profiles (agent_id,display_name,role,specialty,expression,appearance,updated,personality,voice_settings) VALUES (?,?,?,?,?,?,?,?,?)',
                        (agent_id,profile['display_name'],profile['role'],profile['specialty'],profile['expression'],profile['appearance'],time.time(),PERSONALITIES[agent_id],json.dumps(VOICES[agent_id])))
                else:
                    # Migrate old placeholder identities while preserving a user-edited specialty.
                    old=dict(existing);specialty=old.get('specialty') or profile['specialty']
                    if str(old.get('display_name','')).startswith('咕嘎 00'):specialty=profile['specialty']
                    personality=old.get('personality') or PERSONALITIES[agent_id]
                    voices=old.get('voice_settings')
                    if not voices or voices=='{}':voices=json.dumps(VOICES[agent_id])
                    db.execute('UPDATE agent_profiles SET display_name=?,role=?,specialty=?,expression=?,appearance=?,updated=?,personality=?,voice_settings=? WHERE agent_id=?',
                        (profile['display_name'],profile['role'],specialty,profile['expression'],profile['appearance'],time.time(),personality,voices,agent_id))

    @contextmanager
    def connect(self):
        db=sqlite3.connect(str(self.path),timeout=5);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    def profile(self,agent_id):
        agent_id=str(agent_id)
        with self.connect() as db:row=db.execute('SELECT * FROM agent_profiles WHERE agent_id=?',(agent_id,)).fetchone()
        return dict(row) if row else None

    def profiles(self):
        with self.connect() as db:return [dict(r) for r in db.execute('SELECT * FROM agent_profiles ORDER BY agent_id')]

    def update_profile(self,agent_id,specialty=None,expression=None,role=None,personality=None,voice_settings=None):
        profile=self.profile(agent_id)
        if not profile:raise ValueError('未知咕嘎编号')
        specialty=safe_text(specialty) if specialty is not None else profile['specialty']
        role=safe_text(role) if role is not None else profile['role']
        expression=expression if expression in ('normal','happy','sleepy','surprised','sad','playful') else profile['expression']
        if not specialty:raise ValueError('擅长领域不能为空')
        personality=safe_text(personality) if personality is not None else profile['personality']
        if voice_settings is not None:
            if not isinstance(voice_settings,dict):raise ValueError('音色设置必须为对象')
            allowed={'qwen31','qwen30','edge_voice','edge_rate','edge_pitch'}
            if set(voice_settings)-allowed:raise ValueError('未知音色字段')
            if any(not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_+%.:-]{1,100}',v) for v in voice_settings.values()):raise ValueError('音色参数无效')
            voices=json.dumps(voice_settings)
        else:voices=profile['voice_settings']
        with self.connect() as db:db.execute('UPDATE agent_profiles SET specialty=?,expression=?,role=?,updated=?,personality=?,voice_settings=? WHERE agent_id=?',
            (specialty,expression,role,time.time(),personality,voices,str(agent_id)))
        return self.profile(agent_id)

    def add_dialogue(self,agent_id,role,content):
        text=safe_text(content)
        if not text:return
        with self.connect() as db:db.execute('INSERT INTO agent_dialogue(agent_id,role,content,created) VALUES (?,?,?,?)',
            (str(agent_id),str(role),text,time.time()))

    def remember(self,agent_id,content,kind='note',importance=2):
        text=safe_text(content)
        if not text:raise ValueError('记忆内容不能为空')
        importance=max(1,min(5,int(importance)))
        with self.connect() as db:db.execute('INSERT INTO agent_facts(agent_id,kind,content,importance,created) VALUES (?,?,?,?,?)',
            (str(agent_id),str(kind),text,importance,time.time()))

    def shared_turn(self,speaker,role,content):
        text=safe_text(content)
        if not text:return
        with self.connect() as db:db.execute('INSERT INTO shared_dialogue(speaker,role,content,created) VALUES (?,?,?,?)',
            (str(speaker),str(role),text,time.time()))

    def forget(self,agent_id,ident):
        with self.connect() as db:
            count=db.execute('DELETE FROM agent_facts WHERE agent_id=? AND id=?',(str(agent_id),int(ident))).rowcount
        return dict(deleted=bool(count))

    def facts(self,agent_id,limit=30):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT id,kind,content,importance FROM agent_facts WHERE agent_id=? ORDER BY id DESC LIMIT ?',
                                              (str(agent_id),max(1,min(100,int(limit)))))]

    def recent_dialogue(self,agent_id,limit=12):
        with self.connect() as db:rows=list(db.execute('SELECT role,content,created FROM agent_dialogue WHERE agent_id=? ORDER BY id DESC LIMIT ?',(str(agent_id),int(limit))))
        return [dict(r) for r in reversed(rows)]

    def recall(self,agent_id,query='',limit=6):
        q=_tokens(query)
        with self.connect() as db:
            facts=[dict(r) for r in db.execute('SELECT * FROM agent_facts WHERE agent_id=? ORDER BY importance DESC,created DESC LIMIT 120',(str(agent_id),))]
            dialogue=[dict(r) for r in db.execute('SELECT role,content,created FROM agent_dialogue WHERE agent_id=? ORDER BY id DESC LIMIT 40',(str(agent_id),))]
        scored=[]
        now=time.time()
        for i,item in enumerate(facts):
            text=item['content'];overlap=len(q & _tokens(text)) if q else 0
            score=int(item['importance'])*4+overlap*8+(1/(1+i))
            scored.append((score,item['kind'],text,'fact'))
        for item in dialogue:
            text=item['content'];overlap=len(q & _tokens(text)) if q else 0
            age=(now-float(item['created'] or now))/86400
            score=overlap*7+max(0,3-age)*.5+(1 if item['role']=='user' else 0)
            scored.append((score,'dialogue',text,item['role']))
        scored.sort(reverse=True,key=lambda x:x[0]);out=[];seen=set()
        for score,kind,text,role in scored:
            key=text.casefold()
            if key in seen:continue
            seen.add(key);out.append(dict(kind=kind,role=role,content=text[:1600],score=round(score,2)))
            if len(out)>=limit:break
        return out

    def proactive_topics(self,agent_id,limit=4):
        with self.connect() as db:
            rows=list(db.execute("SELECT content,created FROM agent_dialogue WHERE agent_id=? AND role='user' ORDER BY id DESC LIMIT 16",(str(agent_id),)))
            facts=list(db.execute("SELECT content,created FROM agent_facts WHERE agent_id=? ORDER BY importance DESC,created DESC LIMIT 12",(str(agent_id),)))
        topics=[safe_text(r['content'])[:500] for r in rows if len(safe_text(r['content']))>=4]
        topics[:0]=[safe_text(r['content'])[:500] for r in facts if len(safe_text(r['content']))>=4]
        out=[]
        for text in topics:
            if text.casefold() not in {x.casefold() for x in out}:out.append(text)
            if len(out)>=limit:break
        return out

    def status(self):
        with self.connect() as db:
            counts=[]
            for profile in db.execute('SELECT agent_id,display_name,role,specialty FROM agent_profiles ORDER BY agent_id'):
                item=dict(profile);item['dialogue_count']=db.execute('SELECT COUNT(*) FROM agent_dialogue WHERE agent_id=?',(item['agent_id'],)).fetchone()[0]
                item['fact_count']=db.execute('SELECT COUNT(*) FROM agent_facts WHERE agent_id=?',(item['agent_id'],)).fetchone()[0]
                counts.append(item)
        return dict(path=str(self.path),agents=counts)
