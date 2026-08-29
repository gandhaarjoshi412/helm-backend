import pytest
from httpx import ASGITransport, AsyncClient
from apps.api.app.main import app
from apps.api.app.config import settings
from apps.api.app.database import init_db
from apps.api.app.security.rate_limiter import SlidingWindowRateLimiter


@pytest.fixture(autouse=True)
async def setup_test_db():
    await init_db()


@pytest.mark.asyncio
async def test_security_headers():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/health")
        assert res.status_code == 200
        assert res.headers["x-content-type-options"] == "nosniff"
        assert res.headers["x-frame-options"] == "SAMEORIGIN"
        assert res.headers["x-xss-protection"] == "1; mode=block"


@pytest.mark.asyncio
async def test_sliding_window_rate_limiter():
    limiter = SlidingWindowRateLimiter(rpm=3)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        req = client.build_request("GET", "/api/projects")
        # 3 requests allowed
        await limiter.check(req)
        await limiter.check(req)
        await limiter.check(req)

        # 4th request should raise 429
        with pytest.raises(Exception) as exc_info:
            await limiter.check(req)
        assert "429" in str(exc_info.value)


@pytest.mark.asyncio
async def test_api_key_auth_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "HELM_API_KEY", "secret-test-key-123")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Without key should fail 401
        res = await client.get("/api/projects")
        assert res.status_code == 401

        # With wrong key should fail 401
        res = await client.get("/api/projects", headers={"X-API-Key": "wrong-key"})
        assert res.status_code == 401

        # With correct key should succeed
        res = await client.get("/api/projects", headers={"X-API-Key": "secret-test-key-123"})
        assert res.status_code == 200

        # With Bearer token should succeed
        res = await client.get("/api/projects", headers={"Authorization": "Bearer secret-test-key-123"})
        assert res.status_code == 200
