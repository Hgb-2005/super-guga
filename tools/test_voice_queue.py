import threading
import time
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import patch

import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from core.realtime_agent import AgentRealtimeVoice
from core.realtime_voice import PCMBuffer


def build_agent(notices):
    agent=AgentRealtimeVoice.__new__(AgentRealtimeVoice)
    agent._state_lock=threading.RLock()
    agent._stop=threading.Event()
    agent._notices=deque(notices)
    agent._proactive_notices=deque()
    agent._tasks={}
    agent._generating=False
    agent._response_id=None
    agent._audio_request_pending=False
    agent._vad_turn_pending=False
    agent._user_speaking=False
    agent._playing=False
    agent._pcm=PCMBuffer()
    agent._followup_ready=False
    agent._notice_active=False
    agent._notice_seen=[]
    agent._notice_responses=deque(maxlen=8)
    agent._last_activity=time.monotonic()-10
    agent._pending_user_text=None
    agent._input_epoch=0
    agent._input_text=None
    agent._latency=dict(audio_followups=0)
    agent._sync_screen_context=lambda *a,**k:None
    agent._flush_pending_requests=lambda *a,**k:None
    agent.sent=[]
    agent._send=lambda event:agent.sent.append(event)
    return agent


def speech_creates(agent):
    return [event for event in agent.sent if event.get('type')=='response.create']


class ConsecutiveNoticeTests(unittest.TestCase):
    def test_second_notice_waits_while_first_sentence_is_still_playing(self):
        now=time.monotonic()
        agent=build_agent([('第一条播报',now,True,False,False),
                           ('第二条播报',now,True,False,False)])
        agent._process_command(None)
        self.assertEqual(len(speech_creates(agent)),1,'第一条通知应当开始播报')
        # Server finished generating; the sentence is still coming out of the speaker.
        agent._generating=False
        agent._response_id=None
        agent._audio_request_pending=False
        agent._playing=True
        agent._pcm.append(b'\x00'*4096)
        agent._process_command(None)
        agent._process_command(None)
        self.assertEqual(len(speech_creates(agent)),1,'播报未结束时不得开始第二条')

    def test_second_notice_starts_once_the_first_one_is_audibly_finished(self):
        now=time.monotonic()
        agent=build_agent([('第一条播报',now,True,False,False),
                           ('第二条播报',now,True,False,False)])
        agent._process_command(None)
        agent._generating=False;agent._response_id=None
        agent._audio_request_pending=False
        agent._playing=True;agent._pcm.append(b'\x00'*4096)
        agent._process_command(None)
        self.assertEqual(len(speech_creates(agent)),1)
        # Playback really finished: buffer empty and the playing flag settled.
        agent._playing=False;agent._pcm.clear()
        agent._last_activity=time.monotonic()-10
        agent._process_command(None)
        self.assertEqual(len(speech_creates(agent)),2,'播完后第二条通知应当接上')
        self.assertEqual(len(agent._notices),0)

    def test_playing_flag_alone_is_not_enough_when_audio_is_still_buffered(self):
        now=time.monotonic()
        agent=build_agent([('第一条播报',now,True,False,False),
                           ('第二条播报',now,True,False,False)])
        agent._process_command(None)
        agent._generating=False;agent._response_id=None
        agent._audio_request_pending=False
        # Jitter: the callback already flipped to idle, but a chunk is queued.
        agent._playing=False;agent._pcm.append(b'\x00'*2048)
        agent._process_command(None)
        self.assertEqual(len(speech_creates(agent)),1,'缓冲里还有音频时不得插队')

    def test_user_question_still_cuts_in_immediately(self):
        agent=build_agent([])
        agent._playing=True
        agent._pcm.append(b'\x00'*8192)
        agent._cancel_audio=lambda:agent._pcm.clear()
        agent._dispatch_fast=lambda command,**k:None
        with patch('core.realtime_agent.match_fast_command',return_value=dict(matched=True,action='open_app',kind='app',target='浏览器')):
            agent._process_command(('user_text','打开浏览器'))
        self.assertEqual(len(agent._pcm._data),0,'用户新问题应当立刻清空正在播的语音')


if __name__=='__main__':
    unittest.main()
