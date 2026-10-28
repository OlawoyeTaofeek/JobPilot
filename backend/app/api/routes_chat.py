"""POST /chat (start a turn), GET /chat/{id}/stream (SSE events).

HTTP layer only: validate input, call a service, return a response.
No agent or database logic in here.
"""

from fastapi import APIRouter
from app.deps import DbSession

router = APIRouter(prefix="/chat", tags=["chat"])

# TODO: add endpoints, e.g.
# @router.get("")
# async def list_items(user=Depends(current_user)): ...
