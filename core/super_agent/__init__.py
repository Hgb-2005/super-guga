"""个人超级智能体扩展包：屏幕感知 / 桌面命令 / 灵枢记忆 / 汇报组装。

这些模块是「灵枢 × DSH × 咕嘎」深度适配的具身层：
- screen_context：实时共享屏幕（内存 only，前台窗口 2fps 采样 + 变化检测）
- desktop_command：桌面软件命令总线（受控 open/focus/list）
- narration：把 DSH 进度、屏幕摘要、灵枢记忆组装成咕嘎的大白话
- lingshu_bridge / lingshu_memory：灵枢 mdcg 的 Python MCP 桥（读写同一份记忆真源）
"""
