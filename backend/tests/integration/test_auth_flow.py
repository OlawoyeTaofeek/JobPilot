"""Full login -> /auth/me flow against real Postgres (no fakes)."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.repositories.users import create_user
from app.deps import db_session
from app.main import app
from app.security.auth import hash_password

PASSWORD = "goodpass123"


@pytest.fixture
async def api(db):
    """HTTP client for the app, using the test transaction as its DB session."""
    async def override():
        yield db

    app.dependency_overrides[db_session] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


async def test_login_then_me_with_real_database(api, db):
    user = await create_user(db, email="me@example.com", username="taofeek",
                             hashed_password=hash_password(PASSWORD), full_name="Olawoye Taofeek")

    resp = await api.post("/auth/login", data={"username": "taofeek", "password": PASSWORD})
    assert resp.status_code == 200
    token = resp.json()["access_token"]

    me = await api.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["id"] == str(user.id)
    assert "hashed_password" not in me.json()


async def test_login_by_email_with_real_database(api, db):
    await create_user(db, email="me@example.com", username="taofeek",
                      hashed_password=hash_password(PASSWORD))
    resp = await api.post("/auth/login", data={"username": "ME@example.com", "password": PASSWORD})
    assert resp.status_code == 200


async def test_inactive_user_blocked_with_real_database(api, db):
    user = await create_user(db, email="off@example.com", username="offline",
                             hashed_password=hash_password(PASSWORD))
    user.is_active = False
    await db.flush()
    resp = await api.post("/auth/login", data={"username": "offline", "password": PASSWORD})
    assert resp.status_code == 401
