"""MCP 协议全链路 live 测试:像 ZCode 一样经 stdio 起 server 调 generate_asset。

用法: uv run python test_mcp_live.py [描述] [类别]
"""
from __future__ import annotations

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_DIR = r"D:/core/工作流设计/02_游戏素材生成/实现"


async def main() -> None:
    description = sys.argv[1] if len(sys.argv) > 1 else "一瓶红色治疗药水,玻璃瓶软木塞"
    category = sys.argv[2] if len(sys.argv) > 2 else "item"

    params = StdioServerParameters(
        command="uv", args=["--directory", SERVER_DIR, "run", "python", "-m", "app.server"]
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = sorted(t.name for t in (await session.list_tools()).tools)
            print("tools:", tools)

            result = await session.call_tool("generate_asset", {
                "description": description, "category": category, "sizes": [256],
            })
            data = json.loads(result.content[0].text)
            print(json.dumps({k: data.get(k) for k in
                              ("task_id", "status", "image_calls", "review", "notes", "elapsed_s")},
                             ensure_ascii=False, indent=1))
            print("final:", data["finals"][0]["path"])
            print("draft:", data["draft_path"])

            lib = await session.call_tool("list_library", {})
            print("library:", lib.content[0].text[:200])


if __name__ == "__main__":
    asyncio.run(main())
