"""OpenAI 兼容客户端:ChatClient(对话/视觉审核)+ ImageClient(生图)。

生图双通道(live 探测结论与 01_漫画生成 一致:images/generations 主通道返回 url):
1. POST /v1/images/generations → data[0].url 或 b64_json
2. 失败时降级:POST /v1/chat/completions(生图模型当聊天用)→ 从回复文本提取图片 URL

测试注入:两个客户端都接受 httpx.MockTransport,离线全流程可测。
"""
from __future__ import annotations

import base64
import re
import ssl
from typing import Any

import certifi
import httpx

from app.settings import EnvSettings

_URL_RE = re.compile(r"https?://\S+\.(?:png|jpe?g|webp)\S*", re.IGNORECASE)
_URL_RE_LOOSE = re.compile(r"https?://\S+")


_SSL_CTX: ssl.SSLContext | None = None


def _ssl_context() -> ssl.SSLContext:
    """显式用 certifi 的 CA 集合,不依赖进程环境的 CA 配置(模块级缓存,建上下文较贵)。"""
    global _SSL_CTX
    if _SSL_CTX is None:
        _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
    return _SSL_CTX


def _net_kwargs(env: EnvSettings) -> dict:
    """网络确定性:trust_env=False 无视环境变量与 Windows 注册表系统代理
    (注册表残留的坏代理会让 MCP 子进程里 https 校验失败);
    需要代理时在 .env 配 ASSET_HTTP_PROXY 显式指定。"""
    kw: dict = {"verify": _ssl_context(), "trust_env": False}
    if env.http_proxy:
        kw["proxy"] = env.http_proxy
    return kw


class ChatClient:
    def __init__(self, env: EnvSettings, transport: httpx.BaseTransport | None = None):
        self._client = httpx.AsyncClient(
            base_url=env.base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {env.api_key}"},
            timeout=httpx.Timeout(60.0, connect=10.0),
            transport=transport,
            **_net_kwargs(env),
        )
        self.model = env.chat_model

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.3,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> str:
        """model 可覆盖(视觉审核用别的模型);None 用默认对话模型。"""
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        resp = await self._client.post("/chat/completions", json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"chat HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"malformed chat response: {e}") from e

    async def aclose(self) -> None:
        await self._client.aclose()


class ImageClient:
    def __init__(self, env: EnvSettings, transport: httpx.BaseTransport | None = None):
        # 下载图片的 URL 可能是另一主机(探测结论:媒体服务在 :8001),
        # 用无 base_url 的独立客户端;测试注入 transport 时同样接管下载请求
        self._api = httpx.AsyncClient(
            base_url=env.base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {env.api_key}"},
            timeout=httpx.Timeout(120.0, connect=10.0),  # 生图 120s 上限,挂起会拖住整个任务
            transport=transport,
            **_net_kwargs(env),
        )
        self._dl = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0), transport=transport,
            **_net_kwargs(env),
        )
        self.model = env.image_model

    async def generate(self, prompt: str, size: str) -> bytes:
        """生图 → 图片二进制。主通道 images/generations,失败降级 chat。"""
        try:
            return await self._via_images_api(prompt, size)
        except Exception:
            return await self._via_chat_api(prompt, size)

    async def _via_images_api(self, prompt: str, size: str) -> bytes:
        resp = await self._api.post(
            "/images/generations",
            json={"model": self.model, "prompt": prompt, "size": size, "n": 1},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"images HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json().get("data") or []
        if not data:
            raise RuntimeError("images/generations returned empty data")
        item = data[0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        if item.get("url"):
            return await self._download(item["url"])
        raise RuntimeError("no b64_json/url in image response")

    async def _via_chat_api(self, prompt: str, size: str) -> bytes:
        """降级通道:部分代理把生图模型挂在 chat 端点,回复文本里带图片链接。"""
        resp = await self._api.post(
            "/chat/completions",
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": f"{prompt} (image size: {size})"}],
            },
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"image-via-chat HTTP {resp.status_code}: {resp.text[:200]}")
        text = ""
        try:
            text = resp.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            pass
        match = _URL_RE.search(text) or _URL_RE_LOOSE.search(text)
        if not match:
            raise RuntimeError(f"no image url found in chat fallback response: {text[:200]}")
        return await self._download(match.group(0))

    async def _download(self, url: str) -> bytes:
        resp = await self._dl.get(url)
        if resp.status_code >= 400:
            raise RuntimeError(f"image download HTTP {resp.status_code}: {url[:120]}")
        return resp.content

    async def aclose(self) -> None:
        await self._api.aclose()
        await self._dl.aclose()


class EditClient:
    """指令式图生图(StepFun step-image-edit-2 等 OpenAI 兼容 /images/edits)。

    multipart 上传参考图 + 改图指令,输出跟随输入尺寸 → 帧间条件生成的落点:
    动画第 0 帧文生图定锚,后续帧"保持同一角色,仅改姿势"编辑锚帧,
    外观漂移在生成阶段就被压住(而不是只靠后期归一化兜底)。
    采样参数沿用官方示例(cfg_scale=1.0 / steps=8)。
    """

    def __init__(self, env: EnvSettings, transport: httpx.BaseTransport | None = None):
        self._api = httpx.AsyncClient(
            base_url=env.edit_base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {env.edit_api_key}"},
            timeout=httpx.Timeout(180.0, connect=10.0),
            transport=transport,
            **_net_kwargs(env),
        )
        self._dl = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0), transport=transport,
            **_net_kwargs(env),
        )
        self.model = env.edit_model

    async def edit(self, prompt: str, ref_png: bytes, filename: str = "ref.png") -> bytes:
        """参考图 + 指令 → 新图二进制。要求 b64_json;返回 url 时下载兜底。"""
        resp = await self._api.post(
            "/images/edits",
            data={"model": self.model, "prompt": prompt, "response_format": "b64_json",
                  "cfg_scale": "1.0", "steps": "8", "text_mode": "true"},
            files={"image": (filename, ref_png, "image/png")},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"edit HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json().get("data") or []
        if not data:
            raise RuntimeError("images/edits returned empty data")
        item = data[0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        if item.get("url"):
            resp2 = await self._dl.get(item["url"])
            if resp2.status_code >= 400:
                raise RuntimeError(f"edit result download HTTP {resp2.status_code}: {item['url'][:120]}")
            return resp2.content
        raise RuntimeError("no b64_json/url in edit response")

    async def aclose(self) -> None:
        await self._api.aclose()
        await self._dl.aclose()
