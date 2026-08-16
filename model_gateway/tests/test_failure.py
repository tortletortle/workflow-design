"""上游与内部失败:上游 5xx(502)/ 网络失败(502)/ 内部异常(500)。"""
import httpx


async def test_upstream_5xx_returns_502(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "mock-fail-502",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "upstream_error"
    assert "502" in resp.json()["error"]["message"]


async def test_network_failure_returns_502(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "mock-fail-network",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "upstream_error"


async def test_unexpected_exception_returns_500(app, monkeypatch):
    provider = app.state.gateway.providers["mock"]

    async def boom(*args, **kwargs):
        raise RuntimeError("injected catastrophic failure")

    monkeypatch.setattr(provider, "call", boom)

    # ServerErrorMiddleware 处理完 500 响应后会重抛异常,这里关掉透传只看响应
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://gw.test") as c:
        resp = await c.post(
            "/v1/chat/completions",
            json={
                "model": "mock-fast",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "internal_error"
    # 内部异常也不能泄漏在途限流计数
    assert app.state.gateway.limiter.active == 0
