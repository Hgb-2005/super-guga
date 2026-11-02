# Dora 形象来源与处理（2026-10-29）

- 来源：用户本轮提供的高清吃橘子、哭泣、生气三张图。近似重复的生气图未重复计入表情。
- 用户明确允许改用内置图像编辑工具；使用原图作编辑输入，去除“豆包AI生成”字样、背景、桌面及游离装饰，生成透明 PNG。
- 原图未包含完整脚部；白色短身衣、短腿和鞋为生成补全。困倦图是根据清理后的吃橘子图派生，不是用户原有的第四张原画。
- 运行素材：v2/happy.png、sad.png、angry.png、sleepy.png，均为 RGBA，1230×1278。
- normal / eating 沿用 happy；playful 沿用 angry；surprised 沿用 happy。共享贴图的状态不冒充新的原画。
- 原 reference.png、base-cutout.png 保留，manifest.json 控制新版加载。
- 本版为原画加轻微透视、呼吸、身体轻摆、拖动反馈与鼠标关注；不是真正三维模型，没有背面，不做虚构背面的全角度转身。
- 静态、透明边缘与有限动态审核文件见 runtime/multiagent-review/*-v2.*。
