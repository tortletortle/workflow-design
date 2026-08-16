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
    args = parser.parse_args()

    if args.demo or not args.story_file:
        story = DEMO_STORY
    else:
        path = Path(args.story_file)
        if not path.exists():
            print(f"找不到故事文件: {path}", file=sys.stderr)
            sys.exit(1)
        story = path.read_text(encoding="utf-8").strip()

    env, config = load_app_config(args.config)
    out_dir = Path(args.out) if args.out else Path(config.output.dir)

    print(f"故事 {len(story)} 字 | 分镜 {args.panels or config.panels.count} 格 | 输出 {out_dir}/")
    pipeline = ComicPipeline(env, config, out_dir)
    report = asyncio.run(pipeline.run(story, panel_count=args.panels))

    print(report.to_json())
    if report.status == "ok":
        print(f"\n完成 ✓ 漫画: {report.comic_path}")
    elif report.status == "partial":
        print(f"\n部分完成(失败格已渲染占位框)✓ 漫画: {report.comic_path} | 失败格: {report.failed_indexes}")
    else:
        print(f"\n失败: {report.error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
