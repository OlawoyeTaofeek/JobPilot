"""Password hashing and JWT helpers (app/security/auth.py)."""

import uuid
from datetime import timedelta

import jwt
import pytest

from app.config import get_settings
from app.security.auth import (
    ALGORITHM,
    DUMMY_HASH,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


# ---- passwords ------------------------------------------------------------------

def test_hash_is_argon2_and_not_the_password():
    hashed = hash_password("CorrectHorse42")
    assert hashed.startswith("$argon2id$")
    assert "CorrectHorse42" not in hashed


def test_same_password_gets_different_hashes():
    # random salt per hash
    assert hash_password("CorrectHorse42") != hash_password("CorrectHorse42")


def test_verify_password_right_and_wrong():
    hashed = hash_password("CorrectHorse42")
    assert verify_password("CorrectHorse42", hashed) is True
    assert verify_password("correcthorse42", hashed) is False
    assert verify_password("", hashed) is False


def test_dummy_hash_never_matches_a_real_password():
    assert DUMMY_HASH.startswith("$argon2id$")
    assert verify_password("CorrectHorse42", DUMMY_HASH) is False


# ---- tokens ---------------------------------------------------------------------

def test_access_token_roundtrip():
    user_id = uuid.uuid4()
    assert decode_token(create_access_token(user_id)) == user_id


def test_token_payload_has_required_claims():
    user_id = uuid.uuid4()
    payload = jwt.decode(create_access_token(user_id), get_settings().secret_key, algorithms=[ALGORITHM])
    assert payload["sub"] == str(user_id)          # sub must be a string
    assert payload["type"] == "access"
    assert payload["exp"] > payload["iat"]


def test_custom_expiry_is_respected():
    user_id = uuid.uuid4()
    payload = jwt.decode(
        create_access_token(user_id, timedelta(minutes=5)),
        get_settings().secret_key,
        algorithms=[ALGORITHM],
    )
    assert payload["exp"] - payload["iat"] == 300


def test_expired_token_is_rejected():
    token = create_access_token(uuid.uuid4(), timedelta(seconds=-1))
    assert decode_token(token) is None


def test_refresh_token_cannot_be_used_as_access_token():
    user_id = uuid.uuid4()
    refresh = create_refresh_token(user_id)
    assert decode_token(refresh) is None                          # default expects "access"
    assert decode_token(refresh, expected_type="refresh") == user_id


def test_tampered_token_is_rejected():
    token = create_access_token(uuid.uuid4())
    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload}.{signature[:-2]}xx"
    assert decode_token(tampered) is None


def test_token_signed_with_another_key_is_rejected():
    forged = jwt.encode(
        {"sub": str(uuid.uuid4()), "type": "access", "exp": 9999999999, "iat": 0},
        "a-completely-different-secret-key-0123456789abcdef",
        algorithm=ALGORITHM,
    )
    assert decode_token(forged) is None


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "access", "exp": 9999999999},                       # no sub
        {"sub": str(uuid.uuid4()), "exp": 9999999999},               # no type
        {"sub": str(uuid.uuid4()), "type": "access"},                # no exp
        {"sub": "not-a-uuid", "type": "access", "exp": 9999999999},  # bad sub
    ],
)
def test_malformed_payloads_are_rejected(payload):
    token = jwt.encode(payload, get_settings().secret_key, algorithm=ALGORITHM)
    assert decode_token(token) is None


@pytest.mark.parametrize("garbage", ["", "not-a-jwt", "a.b.c"])
def test_garbage_tokens_are_rejected(garbage):
    assert decode_token(garbage) is None
