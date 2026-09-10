from datetime import timedelta

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from twin.api.deps import SessionDep
from twin.api.schemas import WebhookDeliveryResource
from twin.bots.models import WebhookDelivery
from twin.core.time import utcnow

router = APIRouter(prefix="/v1", tags=["webhook-deliveries"])


@router.get("/webhook-deliveries")
async def list_deliveries(
    session: SessionDep,
    bot_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[WebhookDeliveryResource]:
    stmt = select(WebhookDelivery).order_by(WebhookDelivery.created_at.desc()).limit(limit)
    if bot_id is not None:
        stmt = stmt.where(WebhookDelivery.payload["bot_id"].as_string() == bot_id)
    rows = await session.execute(stmt)
    return [WebhookDeliveryResource.model_validate(row) for row in rows.scalars()]


@router.post("/webhook-deliveries/{delivery_id}/replay", status_code=status.HTTP_202_ACCEPTED)
async def replay_delivery(delivery_id: str, session: SessionDep) -> WebhookDeliveryResource:
    delivery = await session.get(WebhookDelivery, delivery_id)
    if delivery is None:
        from twin.core.errors import make_error

        raise make_error("not-found", detail=f"delivery {delivery_id} does not exist")
    delivery.status = "pending"
    delivery.next_try_at = utcnow()
    delivery.dead_at = None
    delivery.lease_token = None
    delivery.lease_expires_at = None
    return WebhookDeliveryResource.model_validate(delivery)


@router.post("/webhook-deliveries/{delivery_id}/skip", status_code=status.HTTP_202_ACCEPTED)
async def skip_delivery(delivery_id: str, session: SessionDep) -> WebhookDeliveryResource:
    delivery = await session.get(WebhookDelivery, delivery_id)
    if delivery is None:
        from twin.core.errors import make_error

        raise make_error("not-found", detail=f"delivery {delivery_id} does not exist")
    delivery.status = "skipped"
    delivery.next_try_at = utcnow() + timedelta(days=30)
    delivery.lease_token = None
    delivery.lease_expires_at = None
    return WebhookDeliveryResource.model_validate(delivery)
