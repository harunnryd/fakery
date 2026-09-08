from fastapi import APIRouter, Depends

from twin.api.deps import require_api_key
from twin.api.routes import bots, health, webhooks

router = APIRouter()
router.include_router(health.router)
router.include_router(bots.router, dependencies=[Depends(require_api_key)])
router.include_router(webhooks.router, dependencies=[Depends(require_api_key)])
