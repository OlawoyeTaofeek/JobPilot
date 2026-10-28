"""Company research results (cached).

HTTP layer only: validate input, call a service, return a response.
No agent or database logic in here.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/research", tags=["research"])

# TODO: add endpoints, e.g.
# @router.get("")
# async def list_items(user=Depends(current_user)): ...
