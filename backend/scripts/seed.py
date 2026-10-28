"""Seed the database with the starting data the app needs.

Right now: your own user account (there is no register route yet).

Run from the backend/ folder:
    uv run python -m scripts.seed

Safe to run many times: if the user already exists, nothing changes.
Credentials come from .env (SEED_EMAIL, SEED_USERNAME, SEED_PASSWORD, SEED_FULL_NAME),
never from this file, so no real password ever lands in git.
"""

import asyncio
import sys

from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db.repositories import users as users_repo
from app.db.session import AsyncSessionLocal, engine
from app.security.auth import hash_password

MIN_PASSWORD_LENGTH = 8

class SeedError(Exception):
    """A problem the person running the script can fix (shown without a traceback)."""

def load_seed_config() -> dict[str, str | None]:
    """Read and validate the seed values from settings / .env."""
    settings = get_settings()
    email = (settings.seed_email or "").strip().lower()
    username = (settings.seed_username or "").strip().lower()
    password = settings.seed_password or ""
    full_name = (settings.seed_full_name or "").strip() or None

    missing = [name for name, value in [("SEED_EMAIL", email),
                                        ("SEED_USERNAME", username),
                                        ("SEED_PASSWORD", password)] if not value]
    if missing:
        raise SeedError(f"Missing in .env: {', '.join(missing)}")
    if "@" not in email:
        raise SeedError(f"SEED_EMAIL doesn't look like an email: {email!r}")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SeedError(f"SEED_PASSWORD must be at least {MIN_PASSWORD_LENGTH} characters.")

    return {"email": email, "username": username, "password": password, "full_name": full_name}


async def seed_user(cfg: dict[str, str | None]) -> None:
    async with AsyncSessionLocal() as db:
        # 1. Idempotency: stop if this email OR username is already taken.
        by_email = await users_repo.get_by_email(db, cfg["email"])
        if by_email:
            print(f"Already seeded: {by_email.email} (id={by_email.id}). Nothing to do.")
            return

        by_username = await users_repo.get_by_username(db, cfg["username"])
        if by_username:
            raise SeedError(
                f"Username {cfg['username']!r} is already used by {by_username.email}. "
                "Change SEED_USERNAME in .env."
            )

        # 2. Create the user (repository flushes, so id is assigned here).
        try:
            user = await users_repo.create_user(
                db,
                email=cfg["email"],
                username=cfg["username"],
                hashed_password=hash_password(cfg["password"]),
                full_name=cfg["full_name"],
            )
            # 3. This script owns the transaction, so it commits.
            await db.commit()
        except IntegrityError as e:
            # Someone created the same email/username between our check and the insert.
            await db.rollback()
            raise SeedError("Email or username already exists (unique constraint).") from e

        print("Created user:")
        print(f"  id       : {user.id}")
        print(f"  email    : {user.email}")
        print(f"  username : {user.username}")


async def main() -> int:
    try:
        cfg = load_seed_config()
        await seed_user(cfg)
        return 0
    except SeedError as e:
        print(f"Seed failed: {e}", file=sys.stderr)
        return 1
    finally:
        # Close pooled connections cleanly (avoids "Event loop is closed" noise on Windows).
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))