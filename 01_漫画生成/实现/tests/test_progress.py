"""测试进度反馈功能。"""
import asyncio
from io import StringIO

from app.progress import ProgressReporter


async def test_progress_reporter_basic():
    """测试基础进度反馈。"""
    output = StringIO()
    reporter = ProgressReporter(output=output)
    
    reporter.start()
    
    async with reporter.stage("测试阶段"):
        await asyncio.sleep(0.1)
    
    reporter.finish(success=True, message="测试完成")
    
    result = output.getvalue()
    assert "🎬 开始生成漫画" in result
    assert "▶️  测试阶段" in result
    assert "✓  测试阶段 完成" in result
    assert "🎉 生成完成" in result


async def test_progress_reporter_with_progress():
    """测试带进度条的阶段。"""
    output = StringIO()
    reporter = ProgressReporter(output=output)
    
    reporter.start()
    
    async with reporter.stage("并发任务", total=4) as update:
        for i in range(4):
            await asyncio.sleep(0.05)
            await update(1)
    
    reporter.finish(success=True)
    
    result = output.getvalue()
    assert "并发任务 (共 4 项)" in result
    assert "4/4" in result
    assert "100%" in result


async def test_progress_reporter_concurrent_safe():
    """测试并发安全性。"""
    output = StringIO()
    reporter = ProgressReporter(output=output)
    
    reporter.start()
    
    async with reporter.stage("并发更新", total=10) as update:
        async def task():
            await asyncio.sleep(0.01)
            await update(1)
        
        # 10 个并发任务同时更新进度
        await asyncio.gather(*(task() for _ in range(10)))
    
    reporter.finish(success=True)
    
    result = output.getvalue()
    # 验证最终计数正确（并发安全）
    assert "10/10" in result
    assert "100%" in result


def test_progress_reporter_silent_mode():
    """测试静默模式。"""
    output = StringIO()
    reporter = ProgressReporter(silent=True, output=output)
    
    reporter.start()
    reporter.warn("警告信息")
    reporter.error("错误信息")
    reporter.finish(success=True)
    
    result = output.getvalue()
    # 静默模式下应该没有输出
    assert result == ""


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
