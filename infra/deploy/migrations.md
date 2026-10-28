# Migrations on deploy
The backend container runs `alembic upgrade head` before starting uvicorn
(see Dockerfile.backend). Only ONE instance should migrate; with several
instances, run migrations as a release command instead.
