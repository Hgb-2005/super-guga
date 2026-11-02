"""Persistent in-process Windows UI Automation adapter with stable snapshot references."""
import hashlib
import threading
import time


class UIAUnavailable(RuntimeError):
    pass


class UIABackend:
    def __init__(self, ttl=30):
        self.ttl=ttl
        self.lock=threading.RLock()
        self.loaded=False
        self.elements={}
        self.revisions={}
        self._load()

    def _load(self):
        try:
            import clr
            from System import IntPtr
            bases=(r'C:/Windows/Microsoft.NET/Framework64/v4.0.30319/WPF',
                   r'C:/Windows/Microsoft.NET/Framework/v4.0.30319/WPF')
            import os
            for base in bases:
                if not os.path.isfile(base+'/UIAutomationClient.dll'):continue
                clr.AddReference(base+'/UIAutomationClient.dll')
                clr.AddReference(base+'/UIAutomationTypes.dll')
                break
            from System.Windows.Automation import (AutomationElement,TreeScope,Condition,TreeWalker,
                InvokePattern,SelectionItemPattern,ValuePattern,TogglePattern,ExpandCollapsePattern,ScrollPattern)
            self.IntPtr=IntPtr;self.AutomationElement=AutomationElement;self.TreeScope=TreeScope;self.Condition=Condition
            self.Patterns=dict(invoke=InvokePattern,select=SelectionItemPattern,set_value=ValuePattern,
                               toggle=TogglePattern,expand=ExpandCollapsePattern,collapse=ExpandCollapsePattern,
                               scroll=ScrollPattern)
            self.walker=TreeWalker.ControlViewWalker
            self.loaded=True
        except Exception as exc:
            raise UIAUnavailable('进程内 UIAutomation 不可用: '+type(exc).__name__) from None

    def _root(self,hwnd):
        return self.AutomationElement.FromHandle(self.IntPtr(int(hwnd)))

    @staticmethod
    def _rect(element):
        try:r=element.Current.BoundingRectangle
        except Exception:return None
        return dict(left=float(r.Left),top=float(r.Top),right=float(r.Right),bottom=float(r.Bottom))

    def _entry(self,element,hwnd,index):
        try:
            c=element.Current
            runtime=list(element.GetRuntimeId())
            identity='|'.join(str(x) for x in runtime)
            ref=hashlib.sha256((str(hwnd)+'|'+identity).encode()).hexdigest()[:20]
            item=dict(ref=ref,name=str(c.Name or '')[:200],id=str(c.AutomationId or '')[:200],
                      type=str(c.ControlType.ProgrammaticName or '').replace('ControlType.',''),
                      class_name=str(c.ClassName or '')[:100],enabled=bool(c.IsEnabled),
                      offscreen=bool(c.IsOffscreen),rect=self._rect(element),
                      patterns=[str(x.ProgrammaticName or '').split('.')[-1] for x in element.GetSupportedPatterns()])
            if not c.IsPassword:
                try:
                    pattern=element.GetCurrentPattern(self.Patterns['set_value'].Pattern)
                    item['value']=str(pattern.Current.Value or '')[:1000]
                except Exception:pass
            return item,element
        except Exception:
            return None,None

    def snapshot(self,hwnd,limit=240,time_budget=2.5):
        hwnd=int(hwnd);limit=max(1,min(int(limit),500))
        with self.lock:
            root=self._root(hwnd)
            now=time.monotonic();items=[]
            self.elements={k:v for k,v in self.elements.items() if now-v[1]<self.ttl}
            queue=[root];visited=0
            while queue and len(items)<limit and time.monotonic()-now<time_budget:
                element=queue.pop(0);visited+=1
                item,_=self._entry(element,hwnd,visited)
                if item:
                    items.append(item);self.elements[item['ref']]=(element,now,hwnd)
                try:child=self.walker.GetFirstChild(element)
                except Exception:child=None
                count=0
                while child is not None and count<80:
                    queue.append(child);count+=1
                    try:child=self.walker.GetNextSibling(child)
                    except Exception:break
            revision=hashlib.sha256((str(hwnd)+str(now)+str(len(items))).encode()).hexdigest()[:16]
            self.revisions[hwnd]=revision
            return dict(hwnd=hwnd,revision=revision,elements=items,truncated=len(items)>=limit,
                        elapsed_seconds=round(time.monotonic()-now,3),
                        hint='后续用 ref 调用 uia，避免重复遍历；状态变化后重新 snapshot')

    def _resolve(self,hwnd,ref=None,name='',automation_id='',control_type=''):
        hwnd=int(hwnd)
        with self.lock:
            cached=self.elements.get(str(ref or ''))
            if cached and cached[2]==hwnd and time.monotonic()-cached[1]<self.ttl:
                return cached[0],dict(ref=ref)
        snapshot=self.snapshot(hwnd)
        for item in snapshot['elements']:
            if ref and item['ref']!=ref:continue
            if name and item['name']!=name:continue
            if automation_id and item['id']!=automation_id:continue
            if control_type and item['type'].lower()!=control_type.lower():continue
            cached=self.elements.get(item['ref'])
            if cached:return cached[0],item
        raise RuntimeError('UIA目标不存在或已过期，请重新 snapshot')

    def act(self,hwnd,operation,*,ref=None,name='',automation_id='',control_type='',value=''):
        with self.lock:
            element,item=self._resolve(hwnd,ref,name,automation_id,control_type)
            try:c=element.Current
            except Exception:raise RuntimeError('UIA元素已失效') from None
            if not c.IsEnabled:raise RuntimeError('UIA控件不可用')
            method=''
            try:
                if operation=='focus':element.SetFocus();method='SetFocus'
                elif operation=='invoke':
                    try:element.GetCurrentPattern(self.Patterns['invoke'].Pattern).Invoke();method='InvokePattern'
                    except Exception:element.GetCurrentPattern(self.Patterns['select'].Pattern).Select();method='SelectionItemPattern'
                elif operation=='select':element.GetCurrentPattern(self.Patterns['select'].Pattern).Select();method='SelectionItemPattern'
                elif operation=='set_value':element.GetCurrentPattern(self.Patterns['set_value'].Pattern).SetValue(str(value)[:4000]);method='ValuePattern'
                elif operation=='toggle':element.GetCurrentPattern(self.Patterns['toggle'].Pattern).Toggle();method='TogglePattern'
                elif operation in ('expand','collapse'):element.GetCurrentPattern(self.Patterns[operation].Pattern).Expand() if operation=='expand' else element.GetCurrentPattern(self.Patterns[operation].Pattern).Collapse();method=operation.title()+'Pattern'
                elif operation=='scroll':element.GetCurrentPattern(self.Patterns['scroll'].Pattern).Scroll(-1,-1);method='ScrollPattern'
                elif operation!='click':raise ValueError('未知UIA操作')
            except RuntimeError:raise
            except Exception:
                if operation not in ('click','invoke'):raise RuntimeError('控件不支持该UIA操作') from None
            rect=self._rect(element)
            fallback=operation in ('click','invoke') and not method
            if fallback and (not rect or rect['right']<=rect['left'] or rect['bottom']<=rect['top']):
                raise RuntimeError('UIA控件没有可点击区域')
            return dict(input_sent=not fallback or bool(rect),action='uia',needs_observe=True,
                        method=method or 'MouseFallback',fallback=fallback,control=item,rect=rect,
                        revision=self.revisions.get(int(hwnd)),message='UIA动作已执行；仍需重新 snapshot 或 observe 验证')

    def wait(self,hwnd,seconds=5,*,ref=None,name='',automation_id='',control_type='',state='exists',expected=None):
        hwnd=int(hwnd);deadline=time.monotonic()+max(.1,min(float(seconds),30))
        while time.monotonic()<=deadline:
            try:
                item=self.snapshot(hwnd)['elements']
                found=None
                for row in item:
                    if ref and row['ref']!=ref:continue
                    if name and row['name']!=name:continue
                    if automation_id and row['id']!=automation_id:continue
                    if control_type and row['type'].lower()!=control_type.lower():continue
                    found=row;break
                if state=='exists' and found:return dict(met=True,element=found)
                if found and state=='enabled' and bool(found['enabled'])==bool(expected):return dict(met=True,element=found)
                if found and state=='offscreen' and bool(found['offscreen'])==bool(expected):return dict(met=True,element=found)
            except Exception:
                pass
            time.sleep(.2)
        return dict(met=False,state=state,elapsed=round(time.monotonic()-(deadline-max(.1,min(float(seconds),30))),2))
