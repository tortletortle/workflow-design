"""超时类:上游超时(504)/ 客户端总超时(408)。"""


async def test_upstream_timeout_returns_504(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "mock-fail-timeout",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 504
    assert resp.json()["error"]["code"] == "upstream_timeout"


async def test_client_total_timeout_returns_408(app):
    # 把总超时压到 50ms,mock-slow 会睡 300ms → 客户端总超时先到
    app.state.gateway.limits.request_timeout = 0.05
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gw.test") as c:
        resp = await c.post(
            "/v1/chat/completions",
            json={
                "model": "mock-slow",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert resp.status_code == 408
    assert resp.json()["error"]["code"] == "client_timeout"
    # 超时后限流计数必须被释放,后续请求不被卡死
    assert app.state.gateway.limiter.active == 0
