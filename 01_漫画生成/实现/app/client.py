"""OpenAI 兼容客户端:ChatClient(对话)+ ImageClient(生图)。

生图双通道(live 探测结论:images/generations 主通道,返回 url):
1. POST /v1/images/generations → data[0].url 或 b64_json
2. 失败时降级:POST /v1/chat/completions(生图模型当聊天用)→ 从回复文本提取图片 URL

测试注入:两个客户端都接受 httpx.MockTransport,离线全流程可测。
"""
from __future__ import annotations

import base64
import re
from typing import Any

import httpx

from app.settings import EnvSettings

_URL_RE = re.compile(r"https?://\S+\.(?:png|jpe?g|webp)\S*", re.IGNORECASE)
_URL_RE_LOOSE = re.compile(r"https?://\S+")


class ChatClient:
    def __init__(self, env: EnvSettings, transport: httpx.BaseTransport | None = None):
        self._client = httpx.AsyncClient(
            base_url=env.base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {env.api_key}"},
            timeout=httpx.Timeout(120.0, connect=10.0),  # 连接 10s 快速失败,防挂起
            transport=transport,
        )
        self.model = env.chat_model

    async def chat(
        self, messages: list[dict], temperature: float = 0.3, max_tokens: int | None = None
    ) -> str:
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": temperature}
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
            timeout=httpx.Timeout(120.0, connect=10.0),  # 生图 120s 上限(原 300s 太宽,挂起会拖住整格)
            transport=transport,
        )
        self._dl = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0), transport=transport
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
