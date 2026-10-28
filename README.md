<div align="center">

# JobPilot

**An agentic job-search assistant and AI labour-market intelligence platform**

Collects real job postings every day, turns each one into structured facts with an LLM, and powers an agent that searches, analyses the market and drafts applications from your real CV. Nothing is ever sent without your approval.

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-pgvector-4169E1?logo=postgresql&logoColor=white)
![OpenAI Agents SDK](https://img.shields.io/badge/OpenAI-Agents%20SDK-412991?logo=openai&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-frontend-000000?logo=nextdotjs&logoColor=white)
![Status](https://img.shields.io/badge/status-active%20development-orange)

</div>

---

## Table of contents

- [Why I built this](#why-i-built-this)
- [What it does](#what-it-does)
- [Architecture](#architecture)
- [How it works](#how-it-works)
- [Data model](#data-model)
- [Key design decisions](#key-design-decisions)
- [Tech stack](#tech-stack)
- [Getting started](#getting-started)
- [Project structure](#project-structure)
- [Testing](#testing)
- [Roadmap](#roadmap)
- [Responsible use](#responsible-use)
- [Author](#author)

---

## Why I built this

I'm looking for AI/ML and agentic AI engineering roles from Nigeria. That search raises questions no job board answers well:

| Problem | What it looks like in practice |
|---|---|
| **"Remote" rarely means remote for me** | Many "remote" roles are really *remote within the US/EU*, or need a work visa. You only find that out on line 40 of the description. |
| **Noise and spam** | Aggregated feeds contain duplicates of the same job across boards, recruiter spam templates and stale reposts. |
| **No view of the market** | Which skills do AI roles actually ask for this month? Which countries hire juniors? Which roles sponsor visas? Boards show listings, not trends. |
| **Applying is repetitive** | Every application needs a tailored CV summary, cover letter and follow-up. It's easy to make things up or lose track. |

JobPilot is my answer. It is a **real system on real data**, not a demo on fake data. Building it end to end is also how I'm learning the full stack of a production agentic AI engineer: data pipelines, LLM extraction, tool-using agents, guardrails, human-in-the-loop approval, RAG, evals and deployment.

### Aim

1. **Collect** AI/ML/data job postings daily from legitimate sources, across Nigeria, the UK, Ireland, the EU, Canada and the Gulf.
2. **Understand** each posting: role family, seniority, work mode, *who* the remote work is open to, visa sponsorship, languages and skills, with evidence and a confidence score.
3. **Answer questions** about the market in plain English ("Which skills grew most for junior ML roles in the UK this month?") using text-to-SQL plus charts.
4. **Help apply honestly.** It matches jobs to my CV with RAG, drafts application kits grounded only in facts from the CV, and **pauses for human approval** before anything leaves the system. It never auto-submits.

---

## What it does

| Area | Feature | Status |
|---|---|---|
| **Collection** | Daily collector with a pluggable `Source` interface | ✅ Built |
| | JSearch (RapidAPI) source: retries, backoff, quota guard, typed errors | ✅ Built |
| | Normalisation to a strict `PostingIn` schema | ✅ Built |
| | Cross-board dedupe fingerprint + idempotent `ON CONFLICT` upsert | ✅ Built |
| | Run log (`collector_runs`): cost, quota, errors; daily request budgets | ✅ Built |
| | LinkedIn via treg (AnyAPI), Greenhouse/Lever/Ashby, Adzuna, Google Jobs SERP | 🟡 Next / ⚪ Planned |
| **Understanding** | `PostingFacts` schema for LLM extraction (strict JSON schema) | ✅ Built |
| | Extraction columns + normalised `posting_skills` table | ✅ Built |
| | Extractor agent + batch backfill + accuracy eval on hand-labelled postings | 🟡 In progress |
| **Agent** | Orchestrator with tools: `search_jobs`, `query_market`, `search_my_profile` (RAG), `draft_application` | ⚪ Planned |
| | Guardrails (scope, read-only SQL, CV fact-check), human approval (pause → approve → resume) | ⚪ Planned |
| | MCP: own `jobs-mcp` server + external MCP tools | ⚪ Planned |
| **API & UI** | FastAPI auth: Argon2 password hashing, JWT access tokens, `/auth/me` | ✅ Built |
| | Streaming chat (SSE), approvals API, Next.js UI (chat, approvals inbox, jobs board, market dashboard) | ⚪ Planned |
| **Quality** | Unit + integration test suite (real Postgres, rolled back per test) | ✅ Built |
| | Evals in CI, tracing (Langfuse), dashboards | ⚪ Planned |

---

## Architecture

![JobPilot architecture](docs/architecture.png)

> The diagram is editable: open [`docs/architecture.excalidraw`](docs/architecture.excalidraw) at [excalidraw.com](https://excalidraw.com) (*Open* → choose the file). Green = built, yellow = in progress, grey dashed = planned.

---

## How it works

### 1. Collection (`app/collector/`)

```
plan ──► fetch ──► normalize ──► fingerprint ──► upsert_many ──► log_run
```

- **Plan.** A list of searches (role × place × country) is rotated day by day, so a small free quota still covers every market over a week.
- **Fetch.** Every source implements one small `Source` protocol (`name`, `fetch(item)`, `normalize(raw, item)`). Adding a new job board means adding one adapter. The collector itself doesn't change.
- **Normalize.** Raw vendor JSON is mapped into `PostingIn`, a strict Pydantic model (`extra="forbid"`) whose limits match the database columns. Bad records are skipped and counted, never crash the run.
- **Fingerprint.** `sha256(company | title | city-or-country)` after Unicode/ASCII normalisation. It flags the *same job* seen on different boards. It is indexed, not unique, so the analytics can choose how to treat duplicates.
- **Upsert.** One `INSERT … ON CONFLICT (source, source_job_id) DO NOTHING RETURNING id`, so re-running a day is safe (idempotent).
- **Log.** Every request (success or failure) writes a `collector_runs` row: fetched / inserted / duplicates / skipped, cost, quota remaining, error. Budgets are enforced from this table.

Failures are isolated: an auth error stops the run, a quota error stops that source, and any other error skips just that search.

### 2. Understanding (`app/collector/facts.py`, in progress)

An LLM reads each new posting and returns a `PostingFacts` object (strict JSON schema): role family, seniority, minimum years, work mode, **remote scope** (worldwide / region / country), visa sponsorship, relocation, clearance, language requirements, skills, salary, a quality flag (e.g. spam template) plus **evidence quotes and a confidence score**. Skills are normalised into `posting_skills` so they can be counted with plain SQL.

### 3. Agent (planned)

Built with the **OpenAI Agents SDK**: an orchestrator with function tools, input/output guardrails, `needs_approval` tools that pause the run and resume from saved `RunState` after a human decision, session memory, and RAG over my CV in **pgvector**.

### 4. API & UI

**FastAPI** (async SQLAlchemy, dependency-injected sessions, OAuth2 password flow + JWT) serves the agent to a **Next.js** front end over streaming (SSE).

---

## Data model

| Table | Purpose |
|---|---|
| `postings` | One row per job per source. Unique on `(source, source_job_id)`. Holds normalised fields, the raw vendor JSON (`JSONB`) and the LLM-extracted facts with version, model, confidence and evidence. |
| `posting_skills` | Normalised skills per posting (`skill`, `raw_skill`), unique per posting and cascade-deleted with it. |
| `collector_runs` | Audit log of every collector request: counts, cost, quota, cache hit, error. |
| `users`, `oauth_credentials` | Accounts and (encrypted) third-party tokens. |

A full column-by-column explanation is in [`docs/milestone-2-data-model.md`](docs/milestone-2-data-model.md).

---

## Key design decisions

- **Source vs provider.** `source` is the platform the job lives on (e.g. LinkedIn), and `provider` is the vendor we fetched it through (e.g. RapidAPI, AnyAPI). Switching vendors doesn't create duplicate jobs.
- **Use the stable ID.** For JSearch I use `job_uid`, not `job_id`, which embeds the search context and changes between queries.
- **Keep the raw payload.** `raw JSONB` means a better normaliser or extractor can be re-run on history without paying for the API again.
- **Version the extraction.** `extraction_version` / `extraction_model` make it possible to re-extract selectively and compare prompts.
- **Short-lived sessions.** One DB session per unit of work (one search, one tool call), committed together with its run log.
- **Timing-safe login.** Unknown users still go through a password verification against a dummy hash, and errors are generic.
- **Humans approve side effects.** The agent can draft, but sending is always a separate tool that requires approval.

---

## Tech stack

| Layer | Tools |
|---|---|
| Language & packaging | Python 3.12+, **uv** |
| API | **FastAPI**, Pydantic v2, pydantic-settings |
| Database | **PostgreSQL + pgvector** (Docker), **SQLAlchemy 2.0 async** (asyncpg), **Alembic** |
| Auth | pwdlib (Argon2), PyJWT, OAuth2 password flow |
| HTTP | httpx (async), retries with exponential backoff |
| Agents | **OpenAI Agents SDK** (tools, guardrails, human approval, sessions, MCP) |
| Testing | pytest, pytest-asyncio, httpx `MockTransport`, integration tests on real Postgres |
| Frontend | Next.js (planned) |
| Ops | Docker Compose, scheduled daily collector |

---

## Getting started

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (running)
- [uv](https://docs.astral.sh/uv/)
- A free [JSearch API key](https://rapidapi.com/letscrunch/api/jsearch) from RapidAPI

### 1. Configure

```bash
git clone https://github.com/<your-username>/jobpilot.git
cd jobpilot
cp .env.example .env        # then fill in SECRET_KEY, JSEARCH_API_KEY, ...
```

| Variable | Description |
|---|---|
| `DATABASE_URL` | e.g. `postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent` |
| `SECRET_KEY` | Long random string used to sign JWTs |
| `JSEARCH_API_KEY` | RapidAPI key for JSearch |
| `JSEARCH_MAX_REQUESTS_PER_DAY` | Daily collector budget (default `6`) |
| `OPENAI_API_KEY` | For extraction and the agent |
| `SEED_EMAIL`, `SEED_USERNAME`, `SEED_PASSWORD`, `SEED_FULL_NAME` | First user created by the seed script |

### 2. Start the database and migrate

```bash
docker compose -f infra/docker-compose.yml up -d db
cd backend
uv sync --extra dev
uv run alembic upgrade head
uv run python -m scripts.seed
```

### 3. Run the API

```bash
uv run uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000/docs, click **Authorize**, log in with the seed user and call `GET /auth/me`.

### 4. Run the collector

```bash
uv run python -m app.collector.run_daily --dry-run --limit 1   # fetch + normalise, write nothing
uv run python -m app.collector.run_daily                       # real run, within the daily budget
```

---

## Project structure

```
jobpilot/
├── backend/
│   ├── app/
│   │   ├── api/                 # FastAPI routers (auth, health)
│   │   ├── collector/
│   │   │   ├── sources/         # Source protocol + JSearch adapter
│   │   │   ├── normalize.py     # raw vendor JSON -> PostingIn
│   │   │   ├── dedupe.py        # cross-board fingerprint
│   │   │   ├── facts.py         # PostingFacts (LLM extraction schema)
│   │   │   └── run_daily.py     # the daily job (CLI)
│   │   ├── db/
│   │   │   ├── models/          # User, Posting, PostingSkill, CollectorRun, ...
│   │   │   ├── repositories/    # query functions (users, postings, collector_runs)
│   │   │   ├── base.py          # DeclarativeBase + naming convention + timestamps
│   │   │   └── session.py       # async engine + session factory
│   │   ├── schemas/             # Pydantic request/response models
│   │   ├── security/            # password hashing + JWT
│   │   ├── config.py            # settings from .env
│   │   ├── deps.py              # DbSession, CurrentUser dependencies
│   │   └── main.py
│   ├── alembic/                 # migrations
│   ├── scripts/                 # seed.py, exploration scripts
│   └── tests/
│       ├── unit/
│       └── integration/         # run against a real Postgres test database
├── frontend/                    # Next.js (planned)
├── infra/                       # docker-compose, Dockerfiles, deploy
└── docs/                        # architecture diagram, data model, ADRs
```

---

## Testing

```bash
cd backend
uv run pytest -m "not integration"     # fast unit tests, no database needed
uv run pytest                          # everything (needs the test database)
```

Integration tests run against a real Postgres database (`jobagent_test`). Each test runs inside a transaction that is rolled back, so tests never leak data into each other. Create the database once:

```bash
docker compose -f infra/docker-compose.yml exec db createdb -U jobagent jobagent_test
```

What is covered: password hashing and JWTs, schema validation, the login route, the JSearch client (retries, quota, error mapping via `httpx.MockTransport`), normalisation, dedupe, plan rotation, repositories, and the full collector run end to end with a scripted source.

---

## Roadmap

- [x] **Milestone 1: Collector.** JSearch source, normalise, dedupe, upsert, run log, budgets, tests
- [ ] **Milestone 2: Understanding.** `PostingFacts` ✅, schema ✅, extractor agent, batch backfill, eval on 30–50 hand-labelled postings
- [ ] **Milestone 3: More sources.** LinkedIn via treg/AnyAPI, Greenhouse/Lever/Ashby boards, Adzuna, Google Jobs
- [ ] **Milestone 4: Agent.** Tools, guardrails, human approval, memory, RAG over CV
- [ ] **Milestone 5: Market analytics.** Text-to-SQL with read-only guardrails, charts
- [ ] **Milestone 6: Applications.** Application kit, approval-gated email, tracker
- [ ] **Milestone 7: UI & deploy.** Next.js, SSE streaming, Docker deploy, tracing, evals in CI

---

## Responsible use

- Data comes only from **official APIs and licensed data providers**, within their terms. There's no scraping behind logins and no use of personal accounts for automation.
- Collection respects budgets and quotas, and every request is logged.
- The agent **never submits applications or sends messages on its own**. Every outbound action needs explicit human approval.
- Generated application text must be grounded in facts from the CV, and the fact-check guardrail exists to stop invented experience.
- Secrets live in `.env` (git-ignored). OAuth tokens are stored encrypted.

---

## Author

**Taofeek**, an aspiring AI/ML and agentic AI engineer based in Nigeria.

<!-- Add your links -->
[LinkedIn](https://www.linkedin.com/in/<your-handle>) · [GitHub](https://github.com/<your-username>) · [Email](mailto:<your-email>)

If this project is useful or interesting to you, a ⭐ is appreciated.
