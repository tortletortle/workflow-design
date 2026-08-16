"""live 实验:④锚帧条件生成 vs 逐帧文生图(对照组=用例集第七节基线)。

walk_side 6 帧,红披风女骑士-侧视卡;.env 已配 ASSET_EDIT_* → animator 自动走编辑通道。
记录:image_calls 分解(t2i/edit 由 notes 与 edit 标记推断)、审核状态、耗时。
"""
from __future__ import annotations

import asyncio
import json
import time

from app.animator import generate_frames
from app.settings import load_app_config


async def main() -> None:
    env, cfg = load_app_config()
    print(f"edit key configured: {bool(env.edit_api_key)} model={env.edit_model}")
    t0 = time.monotonic()
    res = await generate_frames("红披风女骑士-侧视", "walk_side", size_px=128,
                                env=env, cfg=cfg, review=True)
    elapsed = round(time.monotonic() - t0, 1)
    print(f"\nwalk_side(锚帧编辑版): {len(res.frames)}f image_calls={res.image_calls} "
          f"review={res.review_status} edit_used={res.edit_used} elapsed={elapsed}s")
    for n in res.notes:
        print("  note:", n)
    print("spritesheet:", res.spritesheet)
    print("gif:", res.gif)
    (res.frames[0].parent.parent / f"_edit_experiment_{res.frames[0].parent.name}.json").write_text(
        json.dumps({"image_calls": res.image_calls, "edit_used": res.edit_used,
                    "review": res.review_status, "elapsed_s": elapsed, "notes": res.notes},
                   ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
