"""漫画生成 CLI:一次性任务,文本进、条漫出。

用法:
  cd 01_漫画生成/实现
  uv run python -m app.main story.txt                  # 生成 4 格条漫到 out/
  uv run python -m app.main story.txt --panels 6 -o my # 6 格,输出到 my/
  uv run python -m app.main --demo                     # 内置小故事快速试跑
中断后重跑同一输出目录会自动续跑(已生图的格不重复计费)。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from app.pipeline import ComicPipeline
from app.settings import load_app_config
from app.errors import ComicError
from app.progress import ProgressReporter
from app.logger import setup_logger

DEMO_STORY = (
    "深夜的便利店,店员小李正在打盹。自动门'叮'的一声开了,一只会说话的橘猫走了进来,"
    "用人的口气说:'来一份关东煮。' 小李揉揉眼睛以为自己疯了。"
    "橘猫慢条斯理地吃完,从怀里掏出一枚金色的硬币放在柜台上:"
    "'谢谢款待,这是给你守夜人的礼物。' 说完推门消失在夜色里。"
    "第二天清晨,同事来接班,发现柜台上硬币变成了一朵会发光的小花,小李笑而不语。"
)


def main() -> None:
    parser = argparse.ArgumentParser(description="文本 → 漫画条漫生成器")
    parser.add_argument("story_file", nargs="?", help="故事文本文件(UTF-8);不传则用 --demo 内置故事")
    parser.add_argument("-o", "--out", default=None, help="输出目录(默认 config.output.dir)")
    parser.add_argument("--panels", type=int, default=None, help="分镜格数(默认 config.panels.count)")
    parser.add_argument("--config", default=None, help="自定义 config.yaml 路径")
    parser.add_argument("--demo", action="store_true", help="使用内置示例故事")
    parser.add_argument("--timeout", type=int, default=300, help="全局超时时间（秒），默认 300")
    parser.add_argument("--log-level", default=None, help="日志级别 (DEBUG/INFO/WARNING/ERROR)")
    parser.add_argument("--log-format", default=None, choices=["text", "json"], help="日志格式")
    args = parser.parse_args()

    # 初始化日志
    setup_logger(level=args.log_level, format=args.log_format)

    if args.demo or not args.story_file:
        story = DEMO_STORY
    else:
        path = Path(args.story_file)
        if not path.exists():
            print(f"❌ 找不到故事文件: {path}", file=sys.stderr)
            sys.exit(1)
        story = path.read_text(encoding="utf-8").strip()

    try:
        env, config = load_app_config(args.config)
    except Exception as e:
        print(f"❌ 配置加载失败: {e}", file=sys.stderr)
        print("   请检查 .env 和 config.yaml 文件", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out) if args.out else Path(config.output.dir)

    print(f"📖 故事 {len(story)} 字 | 🎬 分镜 {args.panels or config.panels.count} 格 | 📁 输出 {out_dir}/")
    
    reporter = ProgressReporter()
    pipeline = ComicPipeline(env, config, out_dir, reporter=reporter)
    
    try:
        report = asyncio.run(pipeline.run(story, panel_count=args.panels, timeout_s=args.timeout))
    except KeyboardInterrupt:
        print("\n\n⏸️  用户中断，已保存进度到 state.json", file=sys.stderr)
        print("   重新运行相同命令即可续跑", file=sys.stderr)
        sys.exit(130)
    except ComicError as e:
        # 结构化错误：显示用户友好提示
        print(f"\n{e.user_hint}", file=sys.stderr)
        if hasattr(e, 'original') and e.original:
            print(f"\n技术细节: {e.original}", file=sys.stderr)
        sys.exit(1)
    except asyncio.TimeoutError:
        # 超时错误特殊处理
        print(f"\n⏱️  生成超时（>{args.timeout}秒）", file=sys.stderr)
        print(f"   提示:", file=sys.stderr)
        print(f"   1. 减少格数 (当前 {args.panels or config.panels.count} 格)", file=sys.stderr)
        print(f"   2. 增加超时限制 --timeout {args.timeout * 2}", file=sys.stderr)
        print(f"   3. 检查网络连接", file=sys.stderr)
        print(f"   4. 已完成的部分已保存到 {out_dir}/state.json，重跑可续", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        # 未预期错误：显示完整堆栈
        print(f"\n❌ 未预期错误: {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print("\n" + "="*60)
    print(report.to_json())
    print("="*60 + "\n")
    
    if report.status == "ok":
        print(f"✅ 生成完成!")
        print(f"   📄 漫画文件: {report.comic_path}")
        print(f"   ⏱️  总耗时: {report.duration_s}s")
        if report.regen_indexes:
            print(f"   🔄 重生成格: {report.regen_indexes}")
    elif report.status == "partial":
        print(f"⚠️  部分完成 (失败格已渲染占位框)")
        print(f"   📄 漫画文件: {report.comic_path}")
        print(f"   ❌ 失败格: {report.failed_indexes}")
        if report.quota_exhausted:
            print(f"   💡 提示: 生图配额(429)耗尽已熔断")
            print(f"           充值/更换 API Key 后重跑即可续")
        print(f"   ⏱️  总耗时: {report.duration_s}s")
    else:
        print(f"❌ 生成失败: {report.error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
