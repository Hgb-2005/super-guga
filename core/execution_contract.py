"""Deterministic completion checks for tool side effects and verification receipts."""

INPUT_ACTIONS={"click","double_click","drag","scroll","hotkey","type","fill","editor_select_tail","uia"}
VERIFY_ACTIONS={"observe","accessibility"}
ACTION_WORDS=("打开","启动","关闭","读取","修改","写入","保存","运行","安装","下载",
              "删除","移动","复制","重命名","上传","提交","点击","输入","执行","修复",
              "测试","检查窗口","截屏","看屏幕","整理文件","创建文件","发送","发一段","发一句")
QUESTION_WORDS=("如何","怎么","为什么","解释一下","有什么区别","是什么","教我","步骤是")


def requires_execution(prompt):
    text=str(prompt or "").lower()
    return any(word in text for word in ACTION_WORDS) and not any(word in text for word in QUESTION_WORDS)


class ExecutionLedger:
    """Track only externally consequential work. Read-only failures stay recoverable."""
    def __init__(self,goal=''):
        self.expects_send=any(word in str(goal) for word in ('发送','发一段','发一句','发给','发条'))
        self.pending=[]
        self.failures={}
        self.completed=[]
        self.evidence_count=0

    @staticmethod
    def _ok(value):
        return bool(isinstance(value,dict) and value.get("ok") is True)

    def record(self,name,args,value):
        args=args if isinstance(args,dict) else {}
        value=value if isinstance(value,dict) else {}
        action=str(args.get("action",""))
        result=value.get("result") if isinstance(value.get("result"),dict) else {}
        ok=self._ok(value)
        if name not in ("task_plan","self_ask") and ok:
            self.evidence_count+=1
        if name=='applications' and action=='open':
            if ok and result.get('selection_required'):
                self.pending.append(dict(category='application_window',hwnd=None,
                    description='发现多个软件窗口，尚未选择并观察目标窗口'))
            return
        if name in ("wechat_send_self","wechat","web_search","browser"):
            if name=='wechat' and action=='open' and self.expects_send and ok:
                self.pending=[p for p in self.pending if p.get('category')!='wechat_message']
                self.pending.append(dict(category='wechat_message',description='已定位微信会话，但尚未取得发送并回读消息的回执'))
            if name=='wechat' and action=='send' and ok and value.get('verified_message_visible'):
                self.pending=[p for p in self.pending if p.get('category')!='wechat_message']
            if ok:self.failures.pop(name,None)
            else:self.failures[name]=name+" 未获得成功验证回执，不能声称已完成"
            return
        if name=="computer":
            if action=='focus' and ok:
                for item in self.pending:
                    if item.get('category')=='application_window':item['hwnd']=args.get('hwnd')
            if action in INPUT_ACTIONS and ok:
                item=dict(category="computer_input",action=action,hwnd=args.get("hwnd"),
                          description=f"computer.{action} 已发送，尚未复查")
                self.pending.append(item)
                self.failures.pop("computer",None)
            elif action in VERIFY_ACTIONS and ok:
                self.pending=[x for x in self.pending if x.get("category")!="computer_input"]
                observed=args.get('hwnd') or result.get('hwnd')
                if observed:
                    self.pending=[x for x in self.pending if not (x.get('category')=='application_window' and x.get('hwnd')==observed)]
                self.failures.pop("computer",None)
                self.completed.append(dict(category="computer_verified",action=action))
            elif action in INPUT_ACTIONS and not ok:
                self.failures["computer"]=f"computer.{action} 未成功"
            return
        if name=="local_computer":
            if action in ("write","run"):
                category="local_write" if action=="write" else "local_run"
                if ok and (action=="write" and result.get("written") or
                           action=="run" and result.get("exit_code")==0 and not result.get("stopped")):
                    self.failures.pop(category,None)
                    self.completed.append(dict(category=category,action=action,path=args.get("path")))
                else:
                    self.failures[category]=f"local_computer.{action} 没有成功回执"
            return
        if name=="ecosystem":
            if action=="call":
                self.failures.pop("ecosystem",None) if ok else self.failures.update(ecosystem="外部软件工具调用失败")
            return
        if name=="battleground":
            if action=="netcut" and not ok:
                self.failures["battleground_netcut"]="拔线请求失败或未获确认"
            elif action=="netcut" and ok:
                self.failures.pop("battleground_netcut",None)
                self.completed.append(dict(category="battleground_netcut",action=action))

    def blockers(self):
        reasons=[item["description"] for item in self.pending]
        reasons.extend(self.failures.values())
        return reasons

    def summary(self):
        return dict(pending=list(self.pending),failures=dict(self.failures),evidence_count=self.evidence_count,
                    completed=list(self.completed[-30:]))
