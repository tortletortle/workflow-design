"""live:侧视卡 + idle 呼吸 + walk_side 侧视走路(含相邻帧连贯性审核)。"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "实现"))

from app.animator import generate_frames  # noqa: E402
from app.pipeline import generate_asset_pipeline  # noqa: E402
from app.settings import load_app_config  # noqa: E402

ASSETS = Path(__file__).resolve().parent / "assets"


async def main() -> None:
    env, cfg = load_app_config()

    # 1) 侧视角色卡(引用正面卡锁外观,保存为侧视卡)
    r = await generate_asset_pipeline(
        "全身侧面像,朝右站立,完整侧影轮廓", "character", "",
        subject_ref="红披风女骑士", save_subject_as="红披风女骑士-侧视",
        env=env, cfg=cfg)
    print(f"side card: {r['status']} review={r['review'].get('status')} {r['elapsed_s']}s")
    print("  subject:", r["prompt"][:90])

    # 2) idle 呼吸(正面卡)
    idle = await generate_frames("红披风女骑士", "idle", size_px=128, env=env, cfg=cfg)
    print(f"idle: {len(idle.frames)}f calls={idle.image_calls} review={idle.review_status} "
          f"notes={idle.notes}")
    for i, p in enumerate(idle.frames):
        shutil.copyfile(p, ASSETS / f"player_idle{i}.png")

    # 3) walk_side 侧视走路(侧视卡, 6帧+相邻帧连贯性审核)
    walk = await generate_frames("红披风女骑士-侧视", "walk_side", size_px=128,
                                 env=env, cfg=cfg)
    print(f"walk_side: {len(walk.frames)}f calls={walk.image_calls} review={walk.review_status}")
    for n in walk.notes:
        print("  note:", n)
    for i, p in enumerate(walk.frames):
        shutil.copyfile(p, ASSETS / f"player_side{i}.png")
    print("gif:", walk.gif)
    print(json.dumps({"idle_gif": str(idle.gif), "walk_gif": str(walk.gif)},
                     ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
