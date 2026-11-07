# 长语音稳定性与陪伴节奏（2026-11-07）

本轮围绕四件事：启动不再预热 AI 工作区、空闲占用下调、碎碎念可控、长语音不再被自身管道打断。
下面只记录可复现的证据与结论，不写推测。

## 一、15:40 那次到底断在哪（结论已修正）

真实通话日志 `runtime/voice/voice-events-20261107-4964.jsonl`：

```
15:40:23  call_start  provider=qianwen        ← 鉴权通过
15:40:23  connected                            ← 会话建立
15:40:35  conversation.item.create / response.create
15:40:46  connection_failure phase=receive  BufferError  uptime=22.88s
15:40:46  fatal  音频积压超过20秒，请检查输出设备或网络   ← 截图里那句
15:40:46  call_end
15:41:05  call_start / connected               ← 用户重开，正常
15:42:48  disconnect reason=user_stop          ← 用户主动结束，通话 2 分 43 秒
```

结论：**平台、Key、模型一直是通的**。唯一真实缺陷在本地播放缓冲：写死
`超过 20 秒即 raise BufferError`，而这个异常被当成致命错误，直接把整通电话掐掉。
输出设备或网络慢半拍就中招。已改为有界丢弃最旧音频，只提示、不断线。

### 一次误判的记录（必须留档）

16:09 / 16:11 / 16:20 / 16:31 的日志里出现的 `provider=bailian`、
`AllocationQuota.FreeTierOnly`、`invalid_parameter` **不是用户的通话**，是单元测试写的：

- `tools/test_voice_call_e2e.py`、`tools/test_realtime_voice.py` 用
  `CFG=dict(..., provider='bailian', api_key='test-credential', workspace_id='test-space')`
  启动真实引擎（socket 被 mock），`call_start` 事件照样落盘；
- `tools/test_long_voice_stability.py` 注入 `AllocationQuota.FreeTierOnly`，
  `tools/test_realtime_voice.py` 注入 `invalid_parameter`。

而 `ai_config.json` 自 **2026-11-05 11:43** 起未修改，一直是 `provider=qianwen`。
所以"账号额度不足、平台选错"的判断是错的，已作废。

已修：测试进程写 `voice-events-YYYYMMDD-test-<pid>.jsonl`，与真实通话彻底分离。

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
