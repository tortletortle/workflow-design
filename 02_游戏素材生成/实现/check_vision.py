"""视觉模型可用性一键检测(配好 .env 的 ASSET_VISION_* 后运行)。

用法:
    uv run python check_vision.py                # 只测能力探测
    uv run python check_vision.py out/<任务>/draft.png   # 顺便对一张真实图打分
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from app.client import ChatClient
from app.reviewer import Reviewer
from app.settings import load_app_config


async def main() -> int:
    env, _ = load_app_config()
    print(f"vision_model   : {env.vision_model or '(未配置)'}")
    print(f"vision_base_url: {env.vision_base_url or '(用主端点) ' + env.base_url}")
    if not env.vision_model:
        print("\n未配置视觉模型。在 .env 里填 ASSET_VISION_MODEL=<模型名>"
              "(如挂独立服务商,再填 ASSET_VISION_BASE_URL / ASSET_VISION_API_KEY)后重试。")
        return 1

    v_env = env.model_copy(update={
        "base_url": (env.vision_base_url or env.base_url).rstrip("/"),
        "api_key": env.vision_api_key or env.api_key,
    })
    chat = ChatClient(v_env)
    try:
        reviewer = Reviewer(chat, env.vision_model)
        ok = await reviewer.available()
        print(f"\n能力探测: {'可用 ✓' if ok else '不可用 ✗(看不了图或连不上,检查模型名/端点/key)'}")
        if not ok:
            return 1

        target = sys.argv[1] if len(sys.argv) > 1 else ""
        if target and Path(target).is_file():
            verdict = await reviewer.review(Path(target), "smoke test subject", min_score=8)
            print(f"真实图试审: passed={verdict.passed} score={verdict.score} "
                  f"status={verdict.status} issues={verdict.issues!r}")
        elif target:
            print(f"(图片不存在,跳过试审: {target})")
        else:
            print("(未指定图片,跳过试审;可传一张 draft.png 路径试审)")
        return 0
    finally:
        await chat.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
