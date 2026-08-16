"""动画帧生成测试:卡锁定 / 逐帧姿势 / 归一化对齐 / 拼条与 GIF / 异常路径。"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app.animator import ANIM_ACTIONS, frame_prompt, generate_frames
from app.consistency import save_subject

from .conftest import asset_png, small_cfg


def _make_card(tmp_path, name="红披风女骑士"):
    cfg = small_cfg(lib_dir=tmp_path / "lib")
    save_subject(name, "female knight in a red cape wearing silver armor",
                 style="pixel art", cfg=cfg)
    return cfg


async def test_frames_generated_normalized_and_assembled(env, tmp_path):
    import io

    import httpx

    from app.client import ChatClient, ImageClient

    cfg = _make_card(tmp_path)
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        # 每帧给不同颜色的主体(相同帧会被 PIL GIF 差分编码优化掉,测不出多帧)
        if request.url.path.endswith("/images/generations"):
            counter["n"] += 1
            return httpx.Response(200, json={"data": [{"url": f"http://img.test/f{counter['n']}.png"}]})
        counter["n"] += 0
        color = [(200, 30, 30), (30, 200, 30), (30, 30, 200), (200, 200, 30)][counter["n"] % 4]
        img = Image.new("RGB", (96, 96), (255, 255, 255))
        from PIL import ImageDraw
        d = ImageDraw.Draw(img)
        d.rectangle([24, 24, 72, 72], fill=color)
        buf = io.BytesIO(); img.save(buf, "PNG")
        return httpx.Response(200, content=buf.getvalue())

    image = ImageClient(env, transport=httpx.MockTransport(handler))
    chat = ChatClient(env, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})))
    res = await generate_frames("红披风女骑士", "walk", size_px=96,
                                env=env, cfg=cfg, chat=chat, image=image,
                                out_root=tmp_path, review=False)
    assert len(res.frames) == len(ANIM_ACTIONS["walk"].poses) == 4
    assert res.image_calls == 4
    assert res.review_status == "skipped"  # review=False
    # 每帧已归一化:同尺寸画布、RGBA(norm/ 目录下)
    for p in res.frames:
        assert p.parent.name == "norm"
        img = Image.open(p)
        assert img.size == (96, 96) and img.mode == "RGBA"
    task_dir = res.frames[0].parent.parent
    # 拼条宽度 = 帧数 × 画布
    sheet = Image.open(task_dir / "spritesheet.png")
    assert sheet.size == (96 * 4, 96)
    # GIF 存在且可解
    gif = Image.open(task_dir / "preview.gif")
    assert gif.n_frames >= 4
    # task.json 元数据
    meta = (task_dir / "task.json").read_text(encoding="utf-8")
    assert '"type": "animation"' in meta


async def test_frame_prompt_contains_card_and_pose():
    p = frame_prompt("a knight with red cape", "pixel art", "left leg lifted")
    assert "a knight with red cape" in p
    assert "left leg lifted" in p
    assert "pure solid white background" in p


async def test_unknown_action_raises(chat, image, env, tmp_path):
    cfg = _make_card(tmp_path)
    from app.animator import generate_frames as gf

    with pytest.raises(ValueError, match="unknown action"):
        await gf("红披风女骑士", "dance", env=env, cfg=cfg, chat=chat, image=image,
                 out_root=tmp_path, review=False)


async def test_missing_card_raises(chat, image, env, tmp_path):
    cfg = _make_card(tmp_path)
    from app.animator import generate_frames as gf

    with pytest.raises(ValueError, match="subject card not found"):
        await gf("不存在的卡", "walk", env=env, cfg=cfg, chat=chat, image=image,
                 out_root=tmp_path, review=False)


async def test_per_frame_review_loop(env, tmp_path):
    """逐帧审核:第一帧不过带反馈重生成。计数验证。"""
    import httpx

    from app.client import ChatClient, ImageClient

    from .conftest import SPEC_JSON, chat_response

    cfg = _make_card(tmp_path)
    calls = {"gen": 0, "review": 0}

    def chat_handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        if b"game asset reviewer" in body or b"sprite frame reviewer" in body:
            calls["review"] += 1
            ok = calls["review"] > 1  # 第 1 次审核不过,之后过
            return httpx.Response(200, json=chat_response(
                '{"pass": %s, "score": %s, "issues": ""}' % ("true" if ok else "false",
                                                             "9" if ok else "5")))
        return httpx.Response(200, json=chat_response(SPEC_JSON))  # 探测

    def img_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/images/generations"):
            calls["gen"] += 1
            return httpx.Response(200, json={"data": [{"url": "http://img.test/a.png"}]})
        return httpx.Response(200, content=asset_png())

    chat = ChatClient(env, transport=httpx.MockTransport(chat_handler))
    image = ImageClient(env, transport=httpx.MockTransport(img_handler))
    env_v = env.model_copy(update={"vision_model": "v-model"})

    res = await generate_frames("红披风女骑士", "idle", size_px=64,
                                env=env_v, cfg=cfg, chat=chat, image=image,
                                vision_chat=chat, out_root=tmp_path, review=True)
    # idle 2 帧:第 1 帧审核 1 次不过 → 重生成 → 第 2 次过;第 2 帧直接过
    assert calls["gen"] == 3
    assert res.review_status in ("ok", "partial")
    assert len(res.frames) == 2


def _anim_transports(env, colors, edit_ok=True):
    """动画全流程 mock:文生图走 /images/generations,编辑走 /images/edits。

    colors:每帧主体色(逐帧不同,防 GIF 差分编码丢帧)。
    返回 (chat, image, edit, calls)。
    """
    import base64
    import io

    import httpx

    from app.client import ChatClient, EditClient, ImageClient

    calls = {"t2i": 0, "edit": 0}
    idx = {"t2i": 0}

    def _png(color):
        from PIL import ImageDraw
        img = Image.new("RGB", (96, 96), (255, 255, 255))
        ImageDraw.Draw(img).rectangle([24, 24, 72, 72], fill=color)
        buf = io.BytesIO(); img.save(buf, "PNG")
        return buf.getvalue()

    def img_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/images/generations"):
            calls["t2i"] += 1
            return httpx.Response(200, json={"data": [
                {"url": f"http://img.test/t2i{calls['t2i']}.png"}]})
        idx["t2i"] += 1
        return httpx.Response(200, content=_png(colors[idx["t2i"] % len(colors)]))

    def edit_handler(request: httpx.Request) -> httpx.Response:
        calls["edit"] += 1
        if not edit_ok:
            return httpx.Response(401, json={"error": {"message": "bad key"}})
        color = colors[(calls["edit"] + 1) % len(colors)]  # 锚帧用 colors[0],编辑帧从 1 号色起
        return httpx.Response(200, json={"data": [
            {"b64_json": base64.b64encode(_png(color)).decode()}]})

    chat = ChatClient(env, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})))
    image = ImageClient(env, transport=httpx.MockTransport(img_handler))
    edit = EditClient(env, transport=httpx.MockTransport(edit_handler))
    return chat, image, edit, calls


async def test_edit_anchor_flow(env, tmp_path):
    """④图生图:第 0 帧文生图定锚,后续帧全部编辑锚帧(不再文生图)。"""
    cfg = _make_card(tmp_path)
    chat, image, edit, calls = _anim_transports(
        env, [(200, 30, 30), (30, 200, 30), (30, 30, 200), (200, 200, 30)])
    res = await generate_frames("红披风女骑士", "walk", size_px=96,
                                env=env, cfg=cfg, chat=chat, image=image, edit=edit,
                                out_root=tmp_path, review=False)
    assert calls["t2i"] == 1 and calls["edit"] == 3  # walk 4 帧 = 1 锚 + 3 编辑
    assert res.edit_used is True
    assert len(res.frames) == 4
    task_dir = res.frames[0].parent.parent
    assert '"edit": true' in (task_dir / "task.json").read_text(encoding="utf-8")


async def test_edit_failure_falls_back_to_t2i(env, tmp_path):
    """编辑通道失败:自动回退逐帧文生图,置位 sticky,notes 有记录。"""
    cfg = _make_card(tmp_path)
    chat, image, edit, calls = _anim_transports(
        env, [(200, 30, 30), (30, 200, 30), (30, 30, 200), (200, 200, 30)],
        edit_ok=False)
    res = await generate_frames("红披风女骑士", "walk", size_px=96,
                                env=env, cfg=cfg, chat=chat, image=image, edit=edit,
                                out_root=tmp_path, review=False)
    assert res.edit_used is False
    assert calls["t2i"] == 4  # 全部走文生图
    assert any("fallback to text-to-image" in n for n in res.notes)


async def test_edit_not_configured_keeps_t2i(env, tmp_path):
    """未配编辑通道(默认):行为与未接入时完全一致。"""
    cfg = _make_card(tmp_path)
    chat, image, edit, calls = _anim_transports(
        env, [(200, 30, 30), (30, 200, 30), (30, 30, 200), (200, 200, 30)])
    res = await generate_frames("红披风女骑士", "walk", size_px=96,
                                env=env, cfg=cfg, chat=chat, image=image,
                                out_root=tmp_path, review=False)
    assert calls["t2i"] == 4 and calls["edit"] == 0
    assert res.edit_used is False
