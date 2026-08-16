"""正确路径:显式 model + 智能 capability + 响应 metadata 验证。"""


async def test_explicit_model_returns_200_with_metadata(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "mock-fast",
            "messages": [{"role": "user", "content": "你好,网关"}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["model"] == "mock-fast"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert "[mock:mock-fast]" in body["choices"][0]["message"]["content"]
    meta = body["metadata"]
    assert meta["routed_model"] == "mock-fast"
    assert meta["routed_provider"] == "mock"
    assert meta["mode"] == "mock"
    assert meta["latency_ms"] >= 0
    assert meta["request_id"]
    assert body["usage"]["total_tokens"] == (
        body["usage"]["prompt_tokens"] + body["usage"]["completion_tokens"]
    )


async def test_custom_request_id_is_passed_through(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "mock-fast",
            "messages": [{"role": "user", "content": "hi"}],
            "request_id": "trace-abc-123",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["metadata"]["request_id"] == "trace-abc-123"


async def test_smart_routing_each_capability(client):
    expected = {
        "low_latency": "mock-fast",
        "low_cost": "mock-fast",
        "high_quality": "mock-smart",
        "long_context": "mock-smart",
        "vision": "mock-vision",
    }
    for capability, model in expected.items():
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "capability": capability,
                "messages": [{"role": "user", "content": "route me"}],
            },
        )
        assert resp.status_code == 200, f"{capability}: {resp.text}"
        assert resp.json()["metadata"]["routed_model"] == model, capability


async def test_list_models_contains_capabilities_and_limits(client):
    resp = await client.get("/v1/models")
    assert resp.status_code == 200
    models = {m["id"]: m for m in resp.json()["data"]}
    assert models["mock-fast"]["capabilities"] == ["low_latency", "low_cost"]
    assert models["mock-fast"]["max_output"] == 4096
    # mock 模式下:mock provider 启用,真实 provider 未启用
    assert models["mock-fast"]["enabled"] is True
    assert models["gpt-4o"]["enabled"] is False


async def test_capabilities_endpoint_lists_all_rules(client):
    resp = await client.get("/v1/capabilities")
    assert resp.status_code == 200
    caps = resp.json()["capabilities"]
    assert set(caps) == {
        "low_cost",
        "low_latency",
        "high_quality",
        "long_context",
        "vision",
    }
    assert all(isinstance(rule, str) and rule for rule in caps.values())


async def test_health_reports_mode_and_providers(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["mode"] == "mock"
    assert body["providers"]["mock"] is True
    assert body["providers"]["openai"] is False
