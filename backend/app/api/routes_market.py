"""Demand-study data for charts: postings over time, top skills, remote share, salaries.

HTTP layer only: validate input, call a service, return a response.
No agent or database logic in here.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/market", tags=["market"])

# TODO: add endpoints, e.g.
# @router.get("")
# async def list_items(user=Depends(current_user)): ...
