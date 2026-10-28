# On Windows: use Git Bash / WSL, or run the commands by hand.
.PHONY: dev db migrate migration test evals collect types chainlit

db:            ## start only Postgres
	docker compose -f infra/docker-compose.yml up -d db

dev:           ## everything in docker
	docker compose -f infra/docker-compose.yml up --build

api:           ## backend locally with reload
	cd backend && uvicorn app.main:app --reload --port 8000

chainlit:      ## dev UI to test the agent before the Next.js UI exists
	cd backend && chainlit run chainlit_app/app.py -w

migrate:
	cd backend && alembic upgrade head

migration:     ## make migration m="add postings"
	cd backend && alembic revision --autogenerate -m "$(m)"

test:
	cd backend && pytest tests/unit tests/integration -q

evals:         ## prompt regression tests (calls the real model)
	cd backend && python -m tests.evals.run_evals

collect:       ## run the job collector once
	cd backend && python -m app.collector.run_daily

types:         ## regenerate frontend types from FastAPI's OpenAPI schema
	cd backend && python -m scripts.export_openapi > ../frontend/openapi.json
	cd frontend && npx openapi-typescript openapi.json -o lib/types.ts
