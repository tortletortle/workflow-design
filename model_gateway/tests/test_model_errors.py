"""模型/路由错误:model 不存在(404)、无路由意图(400)、capability 无匹配(400)、provider 未启用(503)。"""


async def test_unknown_model_returns_404(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "nonexistent-xyz",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "model_not_found"


async def test_missing_messages_returns_400(client):
    resp = await client.post("/v1/chat/completions", json={"model": "mock-fast"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"


async def test_neither_model_nor_capability_returns_400(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "no_model_or_capability"


async def test_capability_with_no_enabled_match_returns_400(app, client):
    # 临时摘掉唯一的 vision 模型,制造"capability 无任何匹配"
    app.state.gateway.models.pop("mock-vision")
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "capability": "vision",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "no_model_for_capability"


async def test_real_model_in_mock_mode_returns_503(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "provider_disabled"
