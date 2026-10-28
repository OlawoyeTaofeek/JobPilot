"""Login, /auth/me and current_user - with the database faked out.

The repository functions and the DB session are replaced, so these tests run
without Postgres. tests/integration/test_auth_flow.py covers the real thing.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import routes_auth
from app.deps import db_session
from app.main import app
from app.security.auth import create_access_token, create_refresh_token, hash_password

PASSWORD = "goodpass123"


def make_user(**overrides) -> SimpleNamespace:
    user = SimpleNamespace(
        id=uuid.uuid4(),
        email="me@example.com",
        username="taofeek",
        full_name="Olawoye Taofeek",
        is_active=True,
        created_at=datetime.now(UTC),
        hashed_password=hash_password(PASSWORD),
    )
    for key, value in overrides.items():
        setattr(user, key, value)
    return user


class FakeSession:
    """Stands in for AsyncSession: only .get() is used by current_user."""

    def __init__(self, users):
        self._by_id = {u.id: u for u in users}

    async def get(self, model, user_id):
        return self._by_id.get(user_id)


@pytest.fixture
def user():
    return make_user()


@pytest.fixture
def client(monkeypatch, user):
    """TestClient whose DB and repository know exactly one user."""
    users = [user]

    async def fake_get_by_email(db, email):
        return next((u for u in users if u.email == email.strip().lower()), None)

    async def fake_get_by_username(db, username):
        return next((u for u in users if u.username == username.strip().lower()), None)

    async def fake_db_session():
        yield FakeSession(users)

    monkeypatch.setattr(routes_auth, "get_by_email", fake_get_by_email)
    monkeypatch.setattr(routes_auth, "get_by_username", fake_get_by_username)
    app.dependency_overrides[db_session] = fake_db_session
    with TestClient(app) as c:
        c.users = users  # let tests add users (e.g. an inactive one)
        yield c
    app.dependency_overrides.clear()


def login(client, identifier, password=PASSWORD):
    return client.post("/auth/login", data={"username": identifier, "password": password})


# ---- POST /auth/login ------------------------------------------------------------

def test_login_with_username_returns_bearer_token(client, user):
    resp = login(client, "taofeek")
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"].count(".") == 2  # header.payload.signature


def test_login_with_email_is_case_insensitive(client):
    assert login(client, "  ME@Example.com ").status_code == 200


def test_wrong_password_and_unknown_user_get_identical_401(client):
    wrong_pw = login(client, "taofeek", "wrongpass123")
    unknown = login(client, "nobody", PASSWORD)
    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json() == unknown.json()            # no user enumeration
    assert wrong_pw.headers["www-authenticate"] == "Bearer"


def test_inactive_user_cannot_log_in(client):
    client.users.append(make_user(email="off@example.com", username="offline", is_active=False))
    assert login(client, "offline").status_code == 401


def test_login_requires_form_fields(client):
    assert client.post("/auth/login", data={"username": "taofeek"}).status_code == 422


# ---- GET /auth/me (current_user) ------------------------------------------------

def auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_me_returns_current_user_without_password_hash(client, user):
    token = login(client, "taofeek").json()["access_token"]
    resp = client.get("/auth/me", headers=auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(user.id)
    assert body["username"] == "taofeek"
    assert "hashed_password" not in body


def test_me_without_token_is_401(client):
    assert client.get("/auth/me").status_code == 401


@pytest.mark.parametrize("token", ["garbage", "a.b.c"])
def test_me_with_invalid_token_is_401(client, token):
    assert client.get("/auth/me", headers=auth_header(token)).status_code == 401


def test_me_with_expired_token_is_401(client, user):
    expired = create_access_token(user.id, timedelta(seconds=-1))
    assert client.get("/auth/me", headers=auth_header(expired)).status_code == 401


def test_me_rejects_refresh_token(client, user):
    resp = client.get("/auth/me", headers=auth_header(create_refresh_token(user.id)))
    assert resp.status_code == 401


def test_me_with_token_for_deleted_user_is_401(client):
    token = create_access_token(uuid.uuid4())  # valid signature, but no such user
    assert client.get("/auth/me", headers=auth_header(token)).status_code == 401


def test_me_for_user_disabled_after_login_is_401(client, user):
    token = login(client, "taofeek").json()["access_token"]
    user.is_active = False  # disabled after the token was issued
    assert client.get("/auth/me", headers=auth_header(token)).status_code == 401


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
