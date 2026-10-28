"""Print FastAPI's OpenAPI schema (used by `make types`)."""

import json

from app.main import app

print(json.dumps(app.openapi(), indent=2))
