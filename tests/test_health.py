async def test_healthz(env):
    r = await env["client"].get("/healthz")
    assert r.status_code == 200


async def test_readyz_ok(env):
    r = await env["client"].get("/readyz")
    assert r.status_code == 200
    assert r.json() == {"db": "ok", "redis": "ok"}


async def test_readyz_503_when_redis_down(env, monkeypatch):
    async def boom():
        raise ConnectionError

    monkeypatch.setattr(env["redis"], "ping", boom)
    r = await env["client"].get("/readyz")
    assert r.status_code == 503
    assert r.json()["redis"] == "down"
