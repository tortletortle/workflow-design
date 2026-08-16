"""幂等与限流:idempotency_key 重放命中缓存;并发超限 429。"""
import asyncio

import httpx

PAYLOAD = {
    "model": "mock-counted",
    "messages": [{"role": "user", "content": "count me"}],
}


def upstream_calls(app) -> int:
    return app.state.gateway.providers["mock"].call_counts.get("mock-counted", 0)


async def test_idempotency_replay_hits_cache(app, client):
    first = await client.post(
        "/v1/chat/completions", json={**PAYLOAD, "idempotency_key": "idem-1"}
    )
    assert first.status_code == 200
    assert upstream_calls(app) == 1

    second = await client.post(
        "/v1/chat/completions", json={**PAYLOAD, "idempotency_key": "idem-1"}
    )
    assert second.status_code == 200
    # 重放返回上次缓存的原响应,上游没有被再次调用
    assert second.json() == first.json()
    assert upstream_calls(app) == 1


async def test_different_key_calls_upstream_again(app, client):
    for key in ("idem-a", "idem-b"):
        resp = await client.post(
            "/v1/chat/completions", json={**PAYLOAD, "idempotency_key": key}
        )
        assert resp.status_code == 200
    assert upstream_calls(app) == 2


async def test_no_key_always_calls_upstream(app, client):
    for _ in range(3):
        resp = await client.post("/v1/chat/completions", json=PAYLOAD)
        assert resp.status_code == 200
    assert upstream_calls(app) == 3


async def test_concurrency_over_limit_returns_429(app):
    app.state.gateway.limiter.limit = 2

    async def fire(i: int) -> int:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://gw.test"
        ) as c:
            resp = await c.post(
                "/v1/chat/completions",
                json={
                    "model": "mock-slow",  # 300ms 在途,保证并发窗口
                    "messages": [{"role": "user", "content": f"req-{i}"}],
                },
            )
            return resp.status_code

    statuses = await asyncio.gather(*(fire(i) for i in range(8)))
    assert 429 in statuses
    assert statuses.count(429) >= 1
    assert all(s in (200, 429) for s in statuses)
    # 全部结束后在途计数归零
    assert app.state.gateway.limiter.active == 0


async def test_rate_limited_error_body(app):
    app.state.gateway.limiter.limit = 0
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gw.test") as c:
        resp = await c.post(
            "/v1/chat/completions",
            json={
                "model": "mock-fast",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "rate_limited"
