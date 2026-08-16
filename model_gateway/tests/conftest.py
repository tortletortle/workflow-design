import os

# 测试前强制隔离环境:不受本机 .env / GW_* 变量影响
os.environ.pop("GW_MODE", None)
for var in list(os.environ):
    if var.startswith("GW_"):
        os.environ.pop(var)

import httpx
import pytest

from app.main import create_app
from app.settings import Settings


@pytest.fixture
def app():
    return create_app(Settings())


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gw.test") as c:
        yield c
