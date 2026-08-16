"""图生图编辑通道一键检测(配好 .env 的 ASSET_EDIT_API_KEY 后运行)。

用法:
    uv run python check_edit.py                  # 小图改色冒烟(红圈 → 蓝圈)
    uv run python check_edit.py out/<任务>/draft.png "把剑刃改成蓝色火焰"   # 真实图 + 中文指令
"""
from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from app.client import EditClient
from app.settings import load_app_config


def _red_circle() -> bytes:
    img = Image.new("RGB", (256, 256), (255, 255, 255))
    ImageDraw.Draw(img).ellipse((64, 64, 192, 192), fill=(200, 30, 30))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _center_pixel(data: bytes) -> tuple[int, int, int]:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    return img.getpixel((img.width // 2, img.height // 2))


async def main() -> int:
    env, _ = load_app_config()
    print(f"edit_model   : {env.edit_model or '(未配置)'}")
    print(f"edit_base_url: {env.edit_base_url}")
    if not env.edit_api_key:
        print("\n未配置编辑通道。在 .env 里填 ASSET_EDIT_API_KEY=<StepFun key>"
              "(可选 ASSET_EDIT_BASE_URL / ASSET_EDIT_MODEL 覆盖)后重试。"
              "\n未配时动画自动退回逐帧文生图,功能不受影响。")
        return 1

    edit = EditClient(env)
    try:
        # 冒烟:白底红圈 → 指令改蓝,验证 multipart/鉴权/b64 回包全链路
        raw = await edit.edit("Change the red circle to a blue circle. "
                              "Keep everything else unchanged.", _red_circle())
        px = _center_pixel(raw)
        is_blue = px[2] > px[0] + 30
        print(f"\n改色冒烟: {'通过 ✓' if is_blue else '未变色 ✗'} 中心像素={px}")
        if not is_blue:
            Path("check_edit_failed.png").write_bytes(raw)
            print("(结果已存 check_edit_failed.png,人工看图排查)")
            return 1

        target = sys.argv[1] if len(sys.argv) > 1 else ""
        if target and Path(target).is_file():
            prompt = sys.argv[2] if len(sys.argv) > 2 else "保持角色外观完全不变,把手臂抬起"
            out = Path(target).with_name(Path(target).stem + "_edited.png")
            raw = await edit.edit(prompt, Path(target).read_bytes())
            out.write_bytes(raw)
            print(f"真实图试编: 已存 {out} ({len(raw)}B),人工看图验收")
        elif target:
            print(f"(图片不存在,跳过试编: {target})")
        else:
            print("(未指定图片,跳过试编;可传一张 draft.png 路径 + 指令试编)")
        return 0
    finally:
        await edit.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
