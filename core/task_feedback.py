"""Short, factual feedback independent of whether the reasoning model finishes."""
import re
import time
from collections import deque
from pathlib import Path

def acknowledgement(request):
    match=re.search(r'([\w\u4e00-\u9fff .-]+\.py)',request)
    if '冒泡' in request and match:
        name=match.group(1).strip().split('下的')[-1].strip('“”「」 ')
        # Spoken ASR sometimes spells the extension out instead of writing .py.
        name='文件读写.py' if '文件读写' in request else name[-28:]
        return f'你的意思是让我在{name}里写冒泡排序。我先核对当前编辑器，然后开始。'
    summary=re.sub(r'^(用户(?:希望|要求)(?:我)?|请帮我|请)','',request).strip()
    summary=summary.split('。')[0].rstrip('，, ')
    if len(summary)>44:summary=summary[:44]+'…'
    return '收到，你想让我'+summary+'。我现在开始处理。'

def tool_feedback(name,args,result=None):
    if not isinstance(args,dict):args={}
    action=args.get('action','')
    subject=Path(args.get('path','')).name
    labels={('computer','windows'):'正在确认已打开的软件窗口',('computer','editor_context'):'正在定位编辑器的实际工作目录',
        ('computer','focus'):'正在切换到目标窗口',('computer','observe'):'正在查看目标窗口和当前内容',
        ('computer','type'):'正在向编辑器输入代码',('computer','hotkey'):'正在操作编辑器快捷键',
        ('computer','click'):'正在定位编辑区域',('computer','accessibility'):'正在读取窗口控件',
        ('computer','uia'):'正在通过结构化控件执行操作',
        ('computer','editor_select_tail'):'正在选中待修改的函数，保留此前代码',
        ('local_computer','read'):'正在读取'+(subject or '目标文件'),('local_computer','write'):'正在备份并写入'+(subject or '目标文件'),
        ('local_computer','run'):'正在执行命令并等待验证结果',('applications','open'):'正在打开目标软件'}
    summary=labels.get((name,action),'正在执行下一步操作')
    if result is not None:
        payload=result.get('result') if isinstance(result.get('result'),dict) else result
        if name=='applications' and payload.get('selection_required'):
            return '找到了多个已运行窗口，正在选择目标，尚未完成切换。'
        if result.get('ok') and name=='computer' and action in ('click','double_click','drag','scroll','hotkey','type','editor_select_tail','uia'):
            summary=summary.replace('正在','已发送，等待复查：',1)
        elif result.get('ok'):summary=summary.replace('正在','已完成：',1)
        else:summary='这一步没有完成，我会根据错误检查后调整。'
    return summary


class EvidenceProgress:
    """User-facing digest of tool receipts, never of a model's private reasoning."""
    def __init__(self, interval=60, now=None):
        self.interval=interval
        self.last_notice=time.monotonic() if now is None else now
        self.receipts=deque(maxlen=8)
        self.count=0;self.reported=-1;self.pending='等待执行器返回结果';self.stale_reported=False
        self.beat=0

    def tool_start(self,name,args):
        self.pending=tool_feedback(name,args)

    def tool_result(self,name,args,result):
        if not isinstance(result,dict):return
        self.count+=1
        self.stale_reported=False
        # Keep only fixed tool labels; never narrate raw payloads, credentials,
        # reasoning tokens, or the executing model's unverified success claims.
        label=tool_feedback(name,args)
        outcome='工具回执成功' if result.get('ok') is True else '未完成'
        self.receipts.append(label.replace('正在','',1)+'（'+outcome+'）')
        self.pending='等待下一步执行或核验'

    def notice(self, now=None):
        now=time.monotonic() if now is None else now
        if now-self.last_notice<self.interval:return None
        self.last_notice=now
        self.beat+=1
        if self.reported==self.count:
            self.stale_reported=True
            return '这一分钟没有新的操作回执，'+self.pending+'。原因还不能确认，任务尚未完成。'
        self.reported=self.count
        if not self.receipts:
            return '目前还没有收到可核验的操作结果，'+self.pending+'。任务尚未确认完成。'
        return '进展：'+'；'.join(list(self.receipts)[-2:])+'。'+self.pending+'，整个任务尚未确认完成。'
