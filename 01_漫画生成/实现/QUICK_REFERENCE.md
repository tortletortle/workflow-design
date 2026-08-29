# 改进快速参考

## 🎯 核心改进

### 1. 错误处理（`app/errors.py`）

**导入和使用：**
```python
from app.errors import (
    QuotaExhaustedError,  # API 配额耗尽
    NetworkError,         # 网络问题
    APIError,            # API 错误
    ValidationError,     # 配置验证
    ModelOutputError,    # AI 输出格式
    ImageQualityError,   # 图片质检
)

# 抛出错误
raise QuotaExhaustedError("HTTP 429")
raise NetworkError("连接超时", original=e)
raise ValidationError("config.yaml", "格式错误")

# 捕获错误
try:
    result = await some_api_call()
except QuotaExhaustedError as e:
    print(e.user_hint)  # 用户友好提示
    if e.recoverable:
        retry()
```

---

### 2. 配置验证（`app/settings.py`）

**自动验证规则：**
```yaml
# config.yaml
panels:
  count: 4              # 1-20
  
image:
  size: "1024x1024"     # 格式: WIDTHxHEIGHT, 256-2048

concurrency:
  design: 4             # 1-10
  image: 2              # 1-10
  retries: 2            # 0-5

assemble:
  format: "JPEG"        # JPEG 或 PNG
  quality: 90           # 1-100
```

**手动验证：**
```python
from app.settings import load_app_config, ValidationError

try:
    env, config = load_app_config("config.yaml")
except ValidationError as e:
    print(e.user_hint)
```

---

### 3. 进度反馈（`app/progress.py`）

**基本使用：**
```python
from app.progress import ProgressReporter

reporter = ProgressReporter()
reporter.start()

# 无进度条的阶段
with reporter.stage("拆分故事"):
    panels = await split_story(...)

# 有进度条的阶段
with reporter.stage("生成图片", total=4) as update:
    for i, panel in enumerate(panels):
        await generate_panel(panel)
        update(1)  # +1 进度

reporter.finish(success=True, message="保存到 out/comic.jpg")
```

**静默模式（测试用）：**
```python
reporter = ProgressReporter(silent=True)
```

---

## 🔧 CLI 使用

### 正常使用
```bash
# 使用内置 demo
uv run python -m app.main --demo

# 自定义故事
uv run python -m app.main story.txt --panels 6 -o my_output
```

### 错误场景示例

**1. API 配额耗尽：**
```bash
$ uv run python -m app.main --demo

❌ API 配额已用尽
   解决方法:
   1. 检查账户余额并充值
   2. 更换 .env 中的 API Key
   3. 等待配额重置后再试
```

**2. 配置错误：**
```bash
$ uv run python -m app.main --demo

❌ 配置错误: panels.count
   panels.count 必须在 1-20 之间
   请检查 config.yaml 或命令行参数
```

**3. 网络错误：**
```bash
$ uv run python -m app.main --demo

⚠️  网络连接失败: 连接 chat API 失败
   建议:
   1. 检查网络连接
   2. 确认 API 端点可访问
   3. 稍后重试
```

**4. 用户中断：**
```bash
$ uv run python -m app.main --demo
^C
⏸️  用户中断，已保存进度到 state.json
   重新运行相同命令即可续跑
```

---

## 🧪 测试

### 运行所有测试
```bash
uv run pytest tests/ -v
```

### 测试改进功能
```bash
uv run python test_improvements.py
```

### 测试特定错误类型
```python
import pytest
from app.errors import ModelOutputError

def test_model_output_error():
    with pytest.raises(ModelOutputError):
        from app.splitter import parse_panels
        parse_panels("不是 JSON")
```

---

## 📋 待办清单

### 集成进度反馈到 pipeline
```python
# app/pipeline.py

from app.progress import ProgressReporter

class ComicPipeline:
    def __init__(self, ..., reporter: ProgressReporter | None = None):
        self.reporter = reporter or ProgressReporter(silent=True)
    
    async def run(self, story: str, ...) -> PipelineReport:
        self.reporter.start()
        
        with self.reporter.stage("拆分故事"):
            panels = await split_story(...)
        
        with self.reporter.stage("设计提示词", total=len(panels)) as update:
            for panel in panels:
                await design_panel(panel)
                update(1)
        
        with self.reporter.stage("生成图片", total=len(panels)) as update:
            # 并发生成时需要线程安全的计数
            ...
        
        with self.reporter.stage("拼接漫画"):
            assemble_comic(...)
        
        self.reporter.finish(success=True)
```

### 添加结构化日志
```bash
uv add structlog
```

```python
# app/logging.py
import structlog

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)

logger = structlog.get_logger()

# 使用
logger.info("stage_start", stage="splitter", panel_count=4)
logger.warning("retry_attempt", panel=3, attempt=2, error=str(e))
logger.error("quota_exhausted", endpoint="images", status_code=429)
```

---

## 🐛 常见问题

### Q: 测试失败怎么办？
```bash
# 查看详细错误
uv run pytest tests/ -vv --tb=long

# 只运行失败的测试
uv run pytest tests/ --lf
```

### Q: 如何调试错误处理？
```python
# 添加 traceback 输出
import traceback
try:
    ...
except ComicError as e:
    print(e.user_hint)
    traceback.print_exc()  # 显示完整堆栈
```

### Q: 如何自定义错误提示？
```python
# app/errors.py
class CustomError(ComicError):
    def __init__(self, details: str):
        super().__init__(
            message=f"Custom error: {details}",
            user_hint=f"❌ 自定义错误\n   {details}",
            recoverable=True,
        )
```

---

## 📖 代码示例

### 完整的错误处理模式
```python
from app.errors import QuotaExhaustedError, NetworkError, ComicError

async def safe_api_call(client, prompt):
    """带完整错误处理的 API 调用。"""
    try:
        return await client.chat([{"role": "user", "content": prompt}])
    
    except QuotaExhaustedError as e:
        # 配额耗尽：不重试，直接失败
        logger.error("quota_exhausted", error=str(e))
        raise
    
    except NetworkError as e:
        # 网络错误：重试 3 次
        logger.warning("network_error", error=str(e), recoverable=e.recoverable)
        if e.recoverable:
            await asyncio.sleep(2)
            return await safe_api_call(client, prompt)  # 递归重试
        raise
    
    except ComicError as e:
        # 其他已知错误：记录并重新抛出
        logger.error("comic_error", type=type(e).__name__, error=str(e))
        raise
    
    except Exception as e:
        # 未预期错误：包装成 ComicError
        logger.exception("unexpected_error")
        raise ComicError(
            f"未预期错误: {e}",
            user_hint="❌ 发生未知错误，请联系开发者",
            recoverable=False,
        ) from e
```

---

## 🔗 相关链接

- [完整改进方案](./IMPROVEMENTS.md)
- [改进总结](./改进总结.md)
- [测试脚本](./test_improvements.py)

---

**最后更新**: 2026-08-22
