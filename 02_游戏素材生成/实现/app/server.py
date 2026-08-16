"""MCP server 入口:MCPServer(mcp SDK 2.x 高层 API,stdio),注册 4 个工具给 AI 客户端调用。

启动:uv run python -m app.server
注册:mcp_config.example.json(相对路径按实际安装位置改)
"""
from __future__ import annotations

from typing import Any

from mcp.server import MCPServer

from app.consistency import list_library as list_library_impl
from app.pipeline import cutout_image_pipeline, generate_asset_pipeline
from app.pipeline import list_assets as list_assets_impl
from app.settings import load_app_config

mcp = MCPServer("game-asset-generator")


@mcp.tool()
async def generate_asset(
    description: str,
    category: str = "item",
    style: str = "",
    sizes: list[int] | None = None,
    feedback: str = "",
    subject_ref: str = "",
    save_subject_as: str = "",
    save_style_as: str = "",
) -> dict[str, Any]:
    """生成游戏素材,一条龙:请求分解 → 生图(元素类强制白底/背景类满幅) → 视觉审核(可配) → 抠图(背景类跳过) → 多尺寸透明 PNG。

    Args:
        description: 素材描述,中文/英文均可(如"一把像素风格的火焰剑")。
        category: 类别: character | item | icon | tile | effect | ui | background。
            元素类(character/item/icon/effect/ui)输出透明底;
            tile/background 为满幅图,不抠图、保留完整画面。
        style: 美术风格。可以是字面描述("pixel art 16-bit"),也可以是风格库里已保存的
            风格名(见 list_library;先用 save_style_as 存过)。
        sizes: 输出尺寸列表(px,正方形),如 [512, 256, 128];默认 [512, 256, 128]。
        feedback: 对上次结果的修改意见,非空时按意见重生成。
        subject_ref: 主体卡名称——引用已保存的角色/主体外观卡,锁定跨任务同款外观
            (先用 save_subject_as 保存过,见 list_library)。
        save_subject_as: 把当次生成生效的主体描述冻结为主体卡,命名保存,供以后 subject_ref 引用。
        save_style_as: 把当次生效的风格描述冻结为命名风格,供以后 style 按名引用。

    Returns:
        含 task_id、prompt、draft_path(草稿,供查看)、cutout/master 路径、
        finals(各尺寸 PNG 绝对路径;元素类为透明底)、review(审核结论/跳过原因)、notes。
    """
    env, cfg = load_app_config()
    try:
        return await generate_asset_pipeline(
            description=description, category=category, style=style,
            sizes=sizes, feedback=feedback, subject_ref=subject_ref,
            save_subject_as=save_subject_as, save_style_as=save_style_as,
            env=env, cfg=cfg,
        )
    except Exception as e:
        return {"status": "error", "message": f"{type(e).__name__}: {e}"}


@mcp.tool()
async def cutout_image(
    image_path: str,
    sizes: list[int] | None = None,
    padding_ratio: float | None = None,
) -> dict[str, Any]:
    """对已有图片执行抠图(去背景)+ 多尺寸输出(等比缩放、主体居中、透明 PNG)。

    Args:
        image_path: 图片绝对路径(白底或浅底图效果最好)。
        sizes: 输出尺寸列表(px),默认 [512, 256, 128]。
        padding_ratio: 主体四周留白比例(0~0.4),默认 0.06。

    Returns:
        含 cutout(抠图结果路径与所用方法)、master、finals 各尺寸路径。
    """
    _, cfg = load_app_config()
    try:
        return await cutout_image_pipeline(
            image_path=image_path, sizes=sizes, padding_ratio=padding_ratio, cfg=cfg,
        )
    except Exception as e:
        return {"status": "error", "message": f"{type(e).__name__}: {e}"}


@mcp.tool()
async def generate_animation(
    subject_ref: str,
    action: str = "walk",
    size: int = 128,
    review: bool = True,
) -> dict[str, Any]:
    """生成角色动画帧序列:角色卡锁外观 → 第 0 帧文生图定锚 → 后续帧图生图编辑锚帧"同角色仅改姿势"(需配 ASSET_EDIT_*,未配/失败自动回退逐帧文生图)→ 逐帧视觉审核(姿势校验) → 归一化对齐(裁边/统一主体高度/底边对齐,压帧间漂移)→ 横向拼条 spritesheet + 透明 GIF 预览。

    Args:
        subject_ref: 主体卡名称(先用 generate_asset 的 save_subject_as 存过,见 list_library)。
        action: 动作: walk(4帧) | walk_side(6帧侧视) | idle(2帧) | attack(3帧)。
        size: 单帧画布边长(px),默认 128。
        review: 是否逐帧视觉审核(姿势不符自动带反馈重生成)。

    Returns:
        含 frames(各帧路径)、spritesheet(横条)、preview_gif(循环预览)、edit(是否走了图生图条件生成)、审核状态、notes。
    """
    env, cfg = load_app_config()
    try:
        from app.animator import generate_frames

        anim = await generate_frames(subject_ref=subject_ref, action=action, size_px=size,
                                     env=env, cfg=cfg, review=review)
        return {
            "task_id": anim.frames[0].parent.name,
            "status": "ok",
            "action": anim.action,
            "subject": anim.subject,
            "edit": anim.edit_used,
            "image_calls": anim.image_calls,
            "review": {"status": anim.review_status},
            "frames": [str(p.resolve()) for p in anim.frames],
            "spritesheet": str(anim.spritesheet.resolve()),
            "preview_gif": str(anim.gif.resolve()),
            "notes": anim.notes,
        }
    except Exception as e:
        return {"status": "error", "message": f"{type(e).__name__}: {e}"}


@mcp.tool()
async def list_assets(prefix: str = "") -> dict[str, Any]:
    """列出已生成的素材任务(读各任务 task.json 汇总)。

    Args:
        prefix: 按 task_id 前缀过滤(如 "20260816" 看当天任务),空 = 全部。

    Returns:
        含 count 与 assets 数组(task_id/类别/描述/审核状态/终稿路径)。
    """
    _, cfg = load_app_config()
    return list_assets_impl(cfg=cfg, prefix=prefix)


@mcp.tool()
async def list_library() -> dict[str, Any]:
    """查看一致性素材库:已保存的主体/角色卡(跨任务锁定外观用)与命名风格。

    Returns:
        含 styles(名字+锚文本)与 subjects(名字+外观卡+风格+样本任务号)。
    """
    _, cfg = load_app_config()
    return list_library_impl(cfg)


if __name__ == "__main__":
    mcp.run()  # stdio 传输
