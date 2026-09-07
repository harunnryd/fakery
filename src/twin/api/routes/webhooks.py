from fastapi import APIRouter, status

from twin.api.deps import SubscriptionServiceDep
from twin.api.schemas import CreateWebhookRequest, WebhookResource

router = APIRouter(prefix="/v1", tags=["webhooks"])


@router.post("/webhooks", status_code=status.HTTP_201_CREATED)
async def create_webhook(
    body: CreateWebhookRequest, service: SubscriptionServiceDep
) -> WebhookResource:
    subscription = await service.subscribe(str(body.url))
    return WebhookResource.model_validate(subscription)


@router.get("/webhooks")
async def list_webhooks(service: SubscriptionServiceDep) -> list[WebhookResource]:
    subscriptions = await service.subscriptions()
    return [WebhookResource.model_validate(sub) for sub in subscriptions]


@router.delete("/webhooks/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(subscription_id: str, service: SubscriptionServiceDep) -> None:
    await service.unsubscribe(subscription_id)
    return None
