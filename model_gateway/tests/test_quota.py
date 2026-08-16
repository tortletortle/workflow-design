"""配额类错误(413):max_tokens 超上限 / 输入超上下文 / 上游报上下文溢出。"""


async def test_max_tokens_over_model_limit_returns_413(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "mock-fast",  # max_output = 4096
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 9999,
        },
    )
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "quota_exceeded"
    assert "max_tokens" in resp.json()["error"]["message"]


async def test_input_over_small_context_returns_413(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "mock-small-context",  # max_context = 64
            "messages": [{"role": "user", "content": "长" * 500}],
        },
    )
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "quota_exceeded"
    assert "context" in resp.json()["error"]["message"]


async def test_upstream_reported_overflow_returns_413(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "mock-fail-overflow",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "quota_exceeded"


async def test_input_within_limit_still_passes(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "mock-small-context",
            "messages": [{"role": "user", "content": "ok"}],  # ~5 tokens < 64
        },
    )
    assert resp.status_code == 200
