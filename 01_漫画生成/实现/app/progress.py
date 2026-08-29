"""进度反馈系统 - 实时显示工作流进度。

支持:
- 阶段切换通知
- 子任务进度条 (并发生成时显示 3/4 complete)
- 预估剩余时间
- 可选的静默模式(用于测试)
- 并发安全的进度更新
"""
from __future__ import annotations

import asyncio
import sys
import time
from contextlib import asynccontextmanager
from typing import TextIO


class ProgressReporter:
    """轻量级进度报告器,支持嵌套阶段和并发任务计数。"""

    def __init__(self, silent: bool = False, output: TextIO | None = None):
        self.silent = silent
        self.output = output or sys.stderr
        self._stage_start: float = 0
        self._total_start: float = 0

    def start(self) -> None:
        """开始整体流程。"""
        if self.silent:
            return
        self._total_start = time.monotonic()
        print("🎬 开始生成漫画...\n", file=self.output, flush=True)

    @asynccontextmanager
    async def stage(self, name: str, total: int = 0):
        """阶段上下文管理器（异步，并发安全）。

        Args:
            name: 阶段名称 (如 "拆分故事")
            total: 该阶段的任务总数 (用于显示进度,0 表示不显示进度)
        """
        if not self.silent:
            self._stage_start = time.monotonic()
            if total > 0:
                print(f"▶️  {name} (共 {total} 项)...", file=self.output, flush=True)
            else:
                print(f"▶️  {name}...", file=self.output, flush=True)

        completed = {"count": 0}
        lock = asyncio.Lock()  # 🔒 并发安全

        async def update(delta: int = 1) -> None:
            """更新进度 (+delta)，并发安全。"""
            if self.silent or total == 0:
                return
            
            async with lock:
                completed["count"] += delta
                pct = int(100 * completed["count"] / total)
                bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                print(
                    f"\r   [{bar}] {completed['count']}/{total} ({pct}%)",
                    end="",
                    file=self.output,
                    flush=True,
                )

        try:
            yield update
        finally:
            if not self.silent:
                elapsed = time.monotonic() - self._stage_start
                if total > 0:
                    print()  # 换行结束进度条
                print(f"✓  {name} 完成 ({elapsed:.1f}s)\n", file=self.output, flush=True)

    def warn(self, message: str) -> None:
        """显示警告信息。"""
        if not self.silent:
            print(f"⚠️  {message}", file=self.output, flush=True)

    def error(self, message: str) -> None:
        """显示错误信息。"""
        if not self.silent:
            print(f"❌ {message}", file=self.output, flush=True)

    def finish(self, success: bool = True, message: str = "") -> None:
        """结束整体流程。"""
        if self.silent:
            return
        elapsed = time.monotonic() - self._total_start
        if success:
            print(f"🎉 生成完成! 总耗时 {elapsed:.1f}s", file=self.output, flush=True)
            if message:
                print(f"   {message}", file=self.output, flush=True)
        else:
            print(f"💥 生成失败 (耗时 {elapsed:.1f}s)", file=self.output, flush=True)
            if message:
                print(f"   {message}", file=self.output, flush=True)
