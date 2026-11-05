# 软件启动与语音会话修复（2026-11-05）

版本：`2026-11-05.command-session.1`。延续浏览器单步控制的本地快速路径，不新增模型路由或依赖。

## 已确认的问题与修复

1. **操作误进无工具问答。** 原来用整句话是否含“吗/什么/哪里”等判断问答，导致“打开，微信，找到验收联系人，发送，最近怎么样，忙完了吗”被当成提问。现在识别前导指令，并把消息正文视为参数；“怎么打开微信”“微信发送失败是什么问题”仍是普通问答。复合指令保留全文进入有工具的本地执行器，不只打开软件就丢掉余下要求。
2. **简单软件启动范围过窄。** 原快捷入口只认少量别名或手工授权项，忽略完整访问模式已经发现的软件。现在语音标点、礼貌尾词可正常匹配，软件名称在线程中解析，不让开始菜单扫描阻塞 Qt 或收音线程。名称按大小写无关的精确匹配处理；未知名称明确报告，不额外绕模型猜软件。
3. **微信和企业微信错配。** 原窗口查找在进程不匹配时，仍按标题包含“微信”复用企业微信或说明文档。已知软件现在要求对应进程身份；普通微信使用 Weixin/WeChat，而非 WXWork。
4. **回复创建确认前的竞态。** 主动搭话已经请求 `response.create`，但服务端尚未返回 `response.created` 时，新文字输入会清掉生成标记并再次创建回复。新增待确认状态：保留新输入，收到旧回复 ID 后取消并等结束，再创建新回复；迟到的旧结束事件不能解除新请求的保护。离线对照在修改前确实出现两次创建，修改后仅一次。
5. **额度错误被误当断线。** 本次真实服务返回没有 `type` 的顶层 `{code,message,request_id}`，其中 `code=AllocationQuota.FreeTierOnly`。旧握手忽略了它，随后收到连接关闭便反复重连。现在握手和已建立会话均识别此格式，明确提示免费额度已用尽或到期并停止。诊断增加 PID，避免测试和桌宠事件混淆。

保持现有平台、模型、凭据及免费额度保护，没有切到付费，没有修改全局代理设置。

## 验收与限制

- 截图同类指令已通过 Qt 端到端模拟：原话完整到达有电脑操作工具的任务智能体，普通问答、独立闲聊和 DSH 均未启动。联系人与发送接口为隔离模拟，没有向真实联系人发消息。
- 从已加载新版的桌宠本地入口实际执行“打开，微信”：启动线程约 85 ms 返回，之后检测到 `Weixin.exe` 的窗口；没有把企业微信窗口算成通过。这里只验软件启动，没有验登录、联系人搜索或消息发送。
- 静态屏幕资料的修改前 50 秒服务对照为 4 轮回复、0 断线；动态资料加主动搭话的对照出现重复创建、协议拒绝及断开。该时序缺陷可独立复现；不能据此归因所有历史断线。
- 后续服务已明确返回免费额度耗尽：修复后只尝试一次握手，显示额度原因并结束，不反复重连。对应健康通话探测结果仍记为失败，未把额度拒绝包装成通话成功。
- 当前额度阻止持续语音的修复后在线复测；离线时序、错误分类和 Qt 路由检查可完成。恢复有效额度后再进行动态上下文与主动搭话的长通话验收。本次没有真实麦克风声学、数小时通话或任意软件全面保证。

357 项相关回归全部通过，覆盖指令识别、启动线程、软件身份、回复竞态、错误格式、免费保护及原有浏览器/问答/任务能力。既有 GIF `ResourceWarning` 与本次断言无关。

本机证据保留在 `runtime/browser-response-qa/` 与 `runtime/long-voice-qa/`，不公开配置、联系人资料、聊天库或凭据。主要样本为 `command-intent-before.txt`、`pending-response-before.txt`、`installed-wechat-final.json`、`app-launch-before.json`、`dynamic-context-before.json`、`dynamic-context-after.json`、`quota-stop-after.json`。

离线复测：

```powershell
python -m unittest tools.test_command_intent_repair tools.test_personal_harness tools.test_window_discovery tools.test_long_voice_stability tools.test_voice_resilience -q
```

有当前模型的可用免费额度并完成平台保护确认后，可显式进行最长 90 秒、无麦克风的动态服务复测：

```powershell
python tools/probe_long_voice_acceptance.py --live --seconds 60 --changing-context --proactive --label local-dynamic
```

GitHub main、Download ZIP 和 `git pull` 提供本次源码；历史 EXE 未重新打包。
