After `alembic init -t async alembic`:
1. In alembic/env.py: `from app.db.base import Base; import app.db.models` and set
   target_metadata = Base.metadata; read the URL from app.config.get_settings().
2. First migration must run:  op.execute("CREATE EXTENSION IF NOT EXISTS vector")
