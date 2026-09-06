from fastapi import APIRouter

from twin.api.routes import bots, health

router = APIRouter()
router.include_router(health.router)
router.include_router(bots.router)
