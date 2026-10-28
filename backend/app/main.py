"""FastAPI app factory. Run: uvicorn app.main:app --reload"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    routes_approvals, routes_auth, routes_chat, routes_health, routes_jobs,
    routes_market, routes_outreach, routes_profile, routes_research,
)


def create_app() -> FastAPI:
    app = FastAPI(title="Job Agent API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for module in (routes_health, routes_auth, routes_chat, routes_approvals,
                   routes_jobs, routes_research, routes_market,
                   routes_profile, routes_outreach):
        app.include_router(module.router)
    return app


app = create_app()
