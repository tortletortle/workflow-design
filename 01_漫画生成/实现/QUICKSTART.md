# 快速启动指南

## 🚀 一键启动

```bash
cd 01_漫画生成/实现
PYTHONIOENCODING=utf-8 uv run python -m app.main --demo
```

## ✅ 前置检查

### 1. 环境配置 (.env)
```bash
# 查看配置
cat .env

# 需要的信息:
✓ COMIC_BASE_URL       (API端点)
✓ COMIC_API_KEY        (API密钥)
✓ COMIC_CHAT_MODEL     (对话模型 - 已改为 step-2-16k)
✓ COMIC_IMAGE_MODEL    (图像模型 - 已改为 step-1-32k)
```

### 2. 配置文件 (config.yaml)
```bash
# 关键配置已就绪:
✓ panels.count: 4              # 4格漫画
✓ image.consistency: true      # 图生图一致性模式已启用
✓ image.consistency.strength: 0.65
✓ critic.vision_model: step-1-32k
```

### 3. 依赖检查
```bash
uv run python -m app.main --help
```

## 📊 生成流程

```
输入故事文本
    ↓
[1] 故事拆分 (step-2-16k对话模型)
    ↓ 4格分镜描述
[2] 角色卡生成 (step-2-16k对话模型)
    ↓ 详细角色描述
[3] 提示词设计 (step-2-16k对话模型)
    ↓ 100-200词详细提示词
[4] 提示词质检 (step-2-16k对话模型)
    ↓ 9分通过 / 不通过则重设计
[5] 文生图 (第1格，step-1-32k图像模型)
    ↓ panel_01.png
[6] 图生图 (第2-4格，使用panel_01作参考)
    ↓ panel_02.png, panel_03.png, panel_04.png
[7] 启发式质检 (检查图片是否损坏)
    ↓ 通过或重生成
[8] 视觉质检 (step-1-32k视觉模型)
    ↓ 5维度评分，8分通过 / 不通过则保形改图
[9] 拼接成漫画 (JPEG格式)
    ↓ out/comic.jpg
```

## 🎨 实际效果

### 生成参数
- 模型: Step全系列 (对话/图像/编辑)
- 质量: 4K分辨率，JPEG格式
- 耗时: 2-3分钟（取决于网络）

### 生成内容示例
```
第1格: 夜间便利店场景，小李打盹，橘猫走入
  - 角色: 小李(中国职员)、橘猫
  - 光线: 店内灯光+冷色调夜色
  - 构图: 广角建立镜头

第2-4格: 保持小李和橘猫的一致外观
  - 小李发型、衣着、脸型完全相同
  - 橘猫颜色、图案、体型完全相同
  - 风格统一 (黑白漫画风格)
```

## 📝 参数调优

### 一致性强度 (consistency.strength)

```yaml
# config.yaml
image:
  consistency:
    strength: 0.65  # 推荐值
```

- **0.3-0.4**: 严格保持风格，可能缺乏多样性
- **0.6-0.7**: 平衡（推荐）
- **0.8-0.9**: 允许变化，一致性较弱

### 并发数 (concurrency)

```yaml
concurrency:
  design: 4    # 设计并发
  image: 2     # 生图并发（注意API限流）
  retries: 2   # 失败重试次数
```

- **design**: 可增加到6-8
- **image**: 不要超过3（防止API限流）
- **retries**: 2-3次为宜

## 🐛 常见问题

### Q: 生成很慢
A: 检查网络，Step API可能较慢
- 增加 `--timeout` 参数: `--timeout 300`
- 减少 `concurrency.image`: 改为1

### Q: 角色风格不一致
A: 检查consistency配置
```bash
# 确认已启用
grep "enabled: true" config.yaml
# 确认强度合理
grep "strength:" config.yaml
```

### Q: 报错: API 429
A: 配额限制
- 等待30分钟
- 或增加 `concurrency.image` 间隔

### Q: 提示词包含垃圾文本
A: 设计阶段质量问题
- 增加 `retries: 3`
- 等待网络恢复

## 📊 监控与调试

### 查看详细日志
```bash
PYTHONIOENCODING=utf-8 uv run python -m app.main --demo --log-level DEBUG
```

### JSON格式日志
```bash
PYTHONIOENCODING=utf-8 uv run python -m app.main --demo --log-format json
```

### 查看生成过程
```bash
# 生成完后，查看state.json
cat out/state.json | python -m json.tool | less

# 查看提示词 (确认质量)
cat out/state.json | python -c "
import json, sys
data = json.load(sys.stdin)
for p in data['panels']:
    print(f\"Panel {p['index']}: {len(p['image_prompt'])} chars\")
"

# 查看重生成情况
cat out/state.json | python -c "
import json, sys
data = json.load(sys.stdin)
print(f\"Regen: {data.get('regen_indexes', [])}\")
"
```

## ✅ 验证成功

生成成功的标志:
```bash
✅ 生成完成!
   📄 漫画文件: out/comic.jpg
   ⏱️  总耗时: ~150s
   🔄 重生成格: [1, 3, 4] (质检不通过的格子)
```

## 🎯 下一步

### 修改故事重新生成
```bash
# 编辑 app/main.py 中的 DEMO_STORY
# 或使用自定义故事
PYTHONIOENCODING=utf-8 uv run python -m app.main "你的故事文本" --panels 4
```

### 调整生成风格
```yaml
# 修改 config.yaml
image:
  style_anchor: >
    彩色日本漫画, 柔和线条, 温暖色调,
    细致人物表情, 精美背景

  consistency:
    strength: 0.5  # 更严格的一致性
```

### 测试不同参数
```bash
# 增加超时时间
PYTHONIOENCODING=utf-8 uv run python -m app.main --demo --timeout 600

# 增加分镜数
PYTHONIOENCODING=utf-8 uv run python -m app.main --demo --panels 6

# 保存到不同目录
PYTHONIOENCODING=utf-8 uv run python -m app.main --demo -o out_test/
```

## 📞 支持信息

- 模型文档: https://platform.stepfun.com/
- 项目文档: 见 COMPLETION_SUMMARY.md
- 使用指南: 见 README_STEP_USAGE.md
- 迁移文档: 见 STEP_MIGRATION.md

---

**现在可以开始了！** 🎉

```bash
cd 01_漫画生成/实现
PYTHONIOENCODING=utf-8 uv run python -m app.main --demo
```
