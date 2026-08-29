"""游戏素材生成命令行工具 —— 直接调用 generate_asset_pipeline 生成图片素材。

用法:
    python gen_asset.py "一把火焰剑"
    python gen_asset.py "火焰剑" --category item --style "pixel art 16-bit"
    python gen_asset.py "红披风女骑士" --category character --save-knight
    python gen_asset.py --list
    python gen_asset.py --library

类别: character | item | icon | tile | effect | ui | background
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# 确保能导入 app 包
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.pipeline import cutout_image_pipeline, generate_asset_pipeline, list_assets
from app.settings import load_app_config


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="游戏素材生成工具 —— 一句话生成透明底 PNG 素材",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("description", nargs="?", help="素材描述(如'一把火焰剑')")
    p.add_argument("--category", "-c", default="item",
                   choices=["character", "item", "icon", "tile", "effect", "ui", "background"],
                   help="素材类别 (默认 item)")
    p.add_argument("--style", "-s", default="", help="美术风格(如 'pixel art 16-bit')")
    p.add_argument("--sizes", nargs="*", type=int, default=None,
                   help="输出尺寸列表,如 512 256 128")
    p.add_argument("--feedback", "-f", default="", help="修改意见(基于上次结果修改)")
    p.add_argument("--subject", default="", help="引用已保存的主体卡名称(锁定外观)")
    p.add_argument("--save-subject", default="", help="保存主体卡名称(供后续引用)")
    p.add_argument("--save-style", default="", help="保存风格名称(供后续引用)")
    p.add_argument("--list", action="store_true", help="列出已生成的素材")
    p.add_argument("--library", action="store_true", help="查看主体卡/风格库")
    p.add_argument("--cutout", metavar="IMAGE_PATH", help="对已有图片抠图+多尺寸输出")
    return p


async def _generate(args: argparse.Namespace) -> dict:
    env, cfg = load_app_config()
    result = await generate_asset_pipeline(
        description=args.description,
        category=args.category,
        style=args.style,
        sizes=args.sizes,
        feedback=args.feedback,
        subject_ref=args.subject,
        save_subject_as=args.save_subject,
        save_style_as=args.save_style,
        env=env, cfg=cfg,
    )
    return result


async def _cutout(args: argparse.Namespace) -> dict:
    _, cfg = load_app_config()
    return await cutout_image_pipeline(image_path=args.cutout, cfg=cfg)


def _print_result(result: dict) -> None:
    """打印生成结果"""
    if result.get("status") == "error":
        print(f"错误: {result.get('message')}")
        return

    print(f"任务: {result.get('task_id', 'N/A')}")
    print(f"状态: {result.get('status')}")

    if "description" in result:
        print(f"描述: {result['description']}")
    if "category" in result:
        print(f"类别: {result['category']}")
    if "prompt" in result:
        print(f"提示词: {result['prompt']}")

    # 审核信息
    review = result.get("review", {})
    if review.get("status") == "ok":
        print(f"审核: 通过 (模型: {review.get('model', 'N/A')})")
    elif review.get("status") == "failed":
        print("审核: 未通过 (已交付草稿,请查看后决定是否带 feedback 重生成)")
    elif review.get("status") == "skipped":
        print(f"审核: 跳过 ({review.get('reason', '视觉模型未配置')})")

    # 生图次数
    if "image_calls" in result:
        print(f"生图调用: {result['image_calls']} 次")

    # 抠图信息
    cutout_info = result.get("cutout", {})
    if cutout_info.get("method"):
        print(f"抠图方式: {cutout_info['method']}")

    # 输出路径
    print(f"草稿: {result.get('draft_path', 'N/A')}")
    if "master" in result:
        print(f"母版: {result['master']}")
    finals = result.get("finals", [])
    if finals:
        print("终稿:")
        for f in finals:
            if isinstance(f, dict):
                print(f"  {f.get('size', '?')}px: {f.get('path')}")
            else:
                print(f"  {f}")

    # 耗时
    if "elapsed_s" in result:
        print(f"耗时: {result['elapsed_s']}s")

    # 注意事项
    notes = result.get("notes", [])
    if notes:
        print("备注:")
        for n in notes:
            print(f"  - {n}")

    print(f"任务目录: {result.get('task_dir', 'N/A')}")


def _print_assets(assets_result: dict) -> None:
    assets = assets_result.get("assets", [])
    if not assets:
        print("暂无已生成素材")
        return
    print(f"共 {len(assets)} 个任务:")
    for a in assets:
        print(f"  [{a.get('status', '?')}] {a.get('task_id')} - {a.get('description', 'N/A')}")
        if a.get("finals"):
            print(f"    终稿: {', '.join(a['finals'][:2])}{'...' if len(a['finals']) > 2 else ''}")


def _print_library(lib: dict) -> None:
    styles = lib.get("styles", [])
    subjects = lib.get("subjects", [])
    if styles:
        print("风格库:")
        for s in styles:
            print(f"  {s.get('name')}: {s.get('anchor', '')[:60]}")
    else:
        print("风格库: (空)")
    if subjects:
        print("主体卡:")
        for s in subjects:
            print(f"  {s.get('name')}: {s.get('card', '')[:60]}... (风格: {s.get('style', '默认')})")
    else:
        print("主体卡: (空)")


async def main() -> None:
    args = _parser().parse_args()

    # --list: 列出已生成素材
    if args.list:
        _, cfg = load_app_config()
        _print_assets(list_assets(cfg=cfg))
        return

    # --library: 查看库
    if args.library:
        from app.consistency import list_library
        _, cfg = load_app_config()
        _print_library(list_library(cfg))
        return

    # --cutout: 抠图模式
    if args.cutout:
        _print_result(await _cutout(args))
        return

    # 生成模式
    if not args.description:
        _parser().error("需要提供素材描述(或使用 --list / --library / --cutout)")

    result = await _generate(args)
    _print_result(result)


if __name__ == "__main__":
    asyncio.run(main())
