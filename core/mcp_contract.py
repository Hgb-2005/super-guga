"""Shared self-MCP schema and pipe identity, without importing UI or credentials."""
import getpass
import hashlib
import os,re
from pathlib import Path


def pipe_name():
    override=os.environ.get('GUGA_MCP_PIPE','')
    if override and re.fullmatch(r'guga-self-[a-zA-Z0-9-]{1,60}',override):return override
    source = str(Path(__file__).resolve().parents[1]).lower() + getpass.getuser()
    return 'guga-self-' + hashlib.sha256(source.encode('utf-8')).hexdigest()[:20]


def self_tools():
    from core.self_agent import TOOLS
    return [dict(name=t['function']['name'], description=t['function']['description'],
                 inputSchema=t['function']['parameters'])
            for t in TOOLS if t['function']['name'] in ('app_state', 'pet_control', 'workspace', 'applications')]


def dsh_tools():
    from core.self_agent import TOOLS
    allowed={'app_state','pet_control','workspace','applications','computer','local_computer',
             'browser','wechat','web_search','task_plan','music_performance'}
    result=[dict(name=t['function']['name'],description=t['function']['description'],inputSchema=t['function']['parameters'])
            for t in TOOLS if t['function']['name'] in allowed]
    result.extend([
        dict(name='media_workflow',description='本地视频处理：检查、抽帧、中文字幕转写、烧录底部进度条和字幕、生成投稿清单。原文件不覆盖，开始处理不等于完成，wait有限等待；没有发布功能。',
             inputSchema=dict(type='object',properties=dict(action=dict(type='string',enum=['inspect','extract_frames','transcribe','render','status','wait','stop','submission']),
                 path=dict(type='string'),subtitles=dict(type='string'),job_id=dict(type='string'),seconds=dict(type='integer'),
                 title=dict(type='string'),description=dict(type='string')),required=['action'],additionalProperties=False)),
        dict(name='personal_memory',description='用户的持久记忆。只保存明确偏好和可核对事实，不存凭据、隐私或推测。经验候选不自动启用。',
             inputSchema=dict(type='object',properties=dict(action=dict(type='string',enum=['recall','list','remember','forget','propose_lesson','lessons']),
                 query=dict(type='string'),content=dict(type='string'),id=dict(type='integer')),required=['action'],additionalProperties=False)),
        dict(name='task_report',description='回报真实进展或交付；ready只是待验收，不代表独立验证成功。',
             inputSchema=dict(type='object',properties=dict(state=dict(type='string',enum=['progress','ready','blocked','failed','needs_input']),
                 summary=dict(type='string'),milestone=dict(type='boolean')),required=['state','summary'],additionalProperties=False))])
    return result
