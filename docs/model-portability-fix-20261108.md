# 换模型导致多步任务失效的根因（2026-11-08）

## 现象

主人反馈：以前说"打开微信，在搜索框里找到小军，然后发送信息"能做完；
换模型之后同样的话做不到了，怀疑更新把核心能力改没了。

## 根因：换模型没换配套参数，第一个请求就被拒

`core/self_agent.py` 的模型请求里带着 DeepSeek 风格的开关：

```
thinking = disabled  (短任务)
reasoning_effort = low
```

千问的规则是：**关闭思考时 `reasoning_effort` 只能是 `none`**，否则直接返回 400：

```
'reasoning_effort' must be 'none' when 'enable_thinking' is false
```

实测三组对照（同一句话、同一套工具）：

| 参数组合 | 结果 |
|---|---|
| thinking=disabled | 200，返回 1 个 tool_call |
| thinking=disabled + reasoning_effort=none | 200 |
| thinking=disabled + reasoning_effort=low | **400 invalid_parameter_error** |

而触发 400 的条件正好覆盖主人的说法：
`route.category == 'local_task' and len(prompt) < 100` ——
"打开微信，在搜索框里找到小军，然后发送信息"只有 24 个字，被判为短任务，
于是第一个模型请求就挂了，连工具都拿不到，表现就是"什么都不会了"。

以前用 DeepSeek 时它接受这个组合，所以同样的代码能跑。**是模型换了，参数没跟着换。**

## 顺带发现：这个任务其实不需要多步

`TOOLS` 里本来就有 `wechat` 工具，参数是
`action / chat_token / recipient / text`，说明书写明"定位指定聊天对象并发送正文，
执行器先搜索、核对会话和空草稿，再发送并回读"。

也就是说"找小军并发消息"是一次工具调用就能完成的事，不依赖视觉逐步点击。
所以这不是"多步能力丧失"，是**一次都调不出来**。

## 修复

1. `_request` 只在开启思考时才发 `reasoning_effort=high`；关闭思考时不再发这个字段，
   任何兼容网关都接受。
2. 短句里含多步标记（然后／接着／找到／搜索／发送／选择／切换／先…再）时，
   不再按"单步 4 步 / 20 秒"的紧预算跑，改为 20 步 / 40 次操作 / 180 秒，
   并保留"每步必须回执、失败要说明卡在哪"的纪律。
3. 新增 `tools/test_self_agent_payload.py`（4 项）锁定这条契约：
   关闭思考不得携带 effort、开启思考保留 high、工具照常下发。

## 验证

- 130 项回归全通过（含新增 4 项）。
- 真实请求实测：同一句"打开微信找小军发消息"，修复后的参数组合返回 200 并给出
  1 个 `wechat` 工具调用，耗时约 1.3-3 秒。

## 教训

换模型不是只改 `model` 和 `base_url`。思考开关、effort 档位、流式选项、
工具调用协议这些"厂商方言"必须一起验证。以后接入新模型，先在真实工具 schema 下
跑一遍短任务请求，再改默认配置。
