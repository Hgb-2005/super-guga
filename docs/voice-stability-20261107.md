# 长语音稳定性与陪伴节奏（2026-11-07）

本轮围绕四件事：启动不再预热 AI 工作区、空闲占用下调、碎碎念可控、长语音不再被自身管道打断。
下面只记录可复现的证据与结论，不写推测。

## 一、16:09 / 16:11 通话秒断的真实原因

日志：`runtime/voice/voice-events-20261107-35516.jsonl`、`...-23200.jsonl`

```
server_error  code=AllocationQuota.FreeTierOnly  fatal=true
call_start    provider=bailian  model=qwen-audio-3.1-realtime-plus
send          event=session.update
server_error  code=invalid_parameter  fatal=true
connection_failure  phase=handshake  error=ValueError  uptime_seconds=0.02
```

结论：

1. 这两次通话用的是 **阿里云百炼（provider=bailian）**，服务端先返回
   `AllocationQuota.FreeTierOnly`，随后 `session.update` 被拒为 `invalid_parameter`，
   客户端按 fatal 处理，握手在 **0.02 秒**内结束。
2. **与屏幕共享无关**：共享是在 `voice.start()` 之后才起采集线程，握手阶段根本没有截图。
   与上一版 `9c6555c` 对比，`_start_call_screen_share()` 的调用位置与实现完全一致，
   不是本次回归引入的。
3. 复测：把 provider 切回 `qianwen`（千问 AI 平台）后握手正常。

```
provider= qianwen  model= qwen-audio-3.1-realtime-plus
SESSION_UPDATED at 0.1
events= ['session.created', 'session.updated']  errors= []  held_seconds= 20.3
```

因此"不能用"是**账号侧额度/资源分配**问题（免费额度用尽或该模型需付费资源），不是代码链路。

## 二、本次代码改动

| 位置 | 改动 | 原因 |
| --- | --- | --- |
| `core/realtime_voice.py` | `server_error` 事件记录 `param` / `detail`（服务端原文） | 之前只记 `code`，`invalid_parameter` 无法定位到具体字段 |
| `core/realtime_voice.py` | 额度类错误给出平台名 + 服务端原文 + 处理建议；60 秒内不再重连 | 避免"点一次麦克风 → 一串必死握手" |
| `ui/agent_chat.py` | 屏幕采集改为**连接成功回调里**启动，不再压在手握手路径上 | 截图/建线程不再影响"按下麦克风 → 接通" |
| `core/proactive_conversation.py` | 碎碎念默认 **30 秒**，范围 10 秒 — 3 小时 | 10 秒太吵、10 分钟太死板 |
| `ui/pet_window.py` | 菜单"陪伴与碎碎念"改为按秒设置，带 30 秒 / 2 分 / 5 分 / 10 分快捷键，可整体关闭 | 用户要能精确调、能关 |
| `ui/sprite_animator.py` | 空闲帧率 20fps → **10fps**；亚像素头部偏移不再走 125 段网格绘制 | 空闲时不必要的整窗重绘 |
| `core/screen_share.py` | 采集 1fps、视觉 8 秒一次、每分钟≤3 次、OCR 只在视觉失败时兜底 | 屏幕辅助不该比通话本身更贵 |
| `main.py` | Windows 单实例互斥锁 | 重复启动会叠加定时器、采集与线程 |

## 三、实测

启动（离屏，`tools/probe_startup_residency.py`）：

```
build_ms 894.2   agent_panel false   rss 86.6MB   threads 16   idle_cpu 10.9%（单核）
```

空闲 CPU 由改前的 14.8% 降到 10.9%；AI 工作区按需创建（218—233ms），启动时不再预热。

回归：`test_personal_harness` / `test_realtime_voice` / `test_long_voice_stability` /
`test_screen_share` / `test_voice_conversation_controls` / `test_idle_motion_actions` /
`test_companion_modes` / `test_proactive_conversation` 等 194 项通过，界面冒烟 PASS。

## 四、用户可用的两个开关

- 碎碎念：托盘/右键菜单 →「💭 陪伴与碎碎念…」，取消勾选即完全静默。
- 屏幕共享：通话中说"关闭屏幕共享"即可停止本次采集（不影响通话）。
