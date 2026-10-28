"""GET pending approvals, POST /approvals/{id} approve|reject -> resume run.

HTTP layer only: validate input, call a service, return a response.
No agent or database logic in here.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/approvals", tags=["approvals"])

# TODO: add endpoints, e.g.
# @router.get("")
# async def list_items(user=Depends(current_user)): ...
