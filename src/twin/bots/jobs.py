import asyncio
import re
import time
from typing import Any, Protocol

from twin.core.time import SECONDS_PER_MINUTE

JOB_NAME_PREFIX = "botrun"
JOB_MAX_NAME = 63
JOB_TTL_S = 300
JOB_BACKOFF_LIMIT = 0
JOB_STARTUP_GRACE_S = 600
JOB_POLL_S = 10
JOB_SECRET_NAME = "twin-secrets"
JOB_MEMORY_REQUEST = "2Gi"
JOB_MEMORY_LIMIT = "3Gi"
JOB_CPU_REQUEST = "500m"
JOB_XVFB_COMMAND = (
    "Xvfb :99 -screen 0 1280x720x24 -nolisten tcp & sleep 2 && DISPLAY=:99 python -m twin.bot_run"
)


def bot_job_name(bot_id: str) -> str:
    slug = re.sub(r"[^a-z0-9-]", "-", bot_id.lower()).strip("-")
    return f"{JOB_NAME_PREFIX}-{slug}"[:JOB_MAX_NAME].rstrip("-")


def build_bot_job(bot_id: str, image: str, namespace: str, meeting_max_minutes: int) -> dict:
    name = bot_job_name(bot_id)
    labels = {
        "app.kubernetes.io/name": "botrun",
        "app.kubernetes.io/component": "bot",
        "twin.bot/id": bot_id,
    }
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "namespace": namespace, "labels": labels},
        "spec": {
            "backoffLimit": JOB_BACKOFF_LIMIT,
            "ttlSecondsAfterFinished": JOB_TTL_S,
            "activeDeadlineSeconds": meeting_max_minutes * SECONDS_PER_MINUTE + JOB_STARTUP_GRACE_S,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "bot",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": ["sh", "-c", f"{JOB_XVFB_COMMAND} {bot_id}"],
                            "envFrom": [{"secretRef": {"name": JOB_SECRET_NAME}}],
                            "resources": {
                                "requests": {
                                    "memory": JOB_MEMORY_REQUEST,
                                    "cpu": JOB_CPU_REQUEST,
                                },
                                "limits": {"memory": JOB_MEMORY_LIMIT},
                            },
                        }
                    ],
                },
            },
        },
    }


def resolve_job_outcome(job_result: str, db_terminal: bool) -> str | None:
    if db_terminal:
        return None
    if job_result == "timeout":
        return "job-timeout"
    return "job-failed"


class JobClient(Protocol):
    async def create_job(self, manifest: dict[str, Any]) -> None: ...

    async def wait_terminal(self, namespace: str, name: str, timeout_s: int) -> str: ...

    async def delete_job(self, namespace: str, name: str) -> None: ...

    async def aclose(self) -> None: ...


class K8sJobClient:
    def __init__(self, namespace: str) -> None:
        self._namespace = namespace
        self._client: Any = None
        self._api: Any = None

    async def _batch(self) -> Any:
        if self._api is None:
            from kubernetes_asyncio import client, config

            try:
                config.load_incluster_config()
            except Exception:
                config.load_kube_config()
            self._client = client.ApiClient()
            self._api = client.BatchV1Api(self._client)
        return self._api

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
            self._api = None

    async def create_job(self, manifest: dict[str, Any]) -> None:
        api = await self._batch()
        await api.create_namespaced_job(self._namespace, manifest)

    async def wait_terminal(self, namespace: str, name: str, timeout_s: int) -> str:
        from kubernetes_asyncio.client import ApiException

        api = await self._batch()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                job = await api.read_namespaced_job_status(name, namespace)
            except ApiException as err:
                if err.status == 404:
                    return "failed"
                raise
            if getattr(job.status, "succeeded", 0):
                return "completed"
            if getattr(job.status, "failed", 0):
                return "failed"
            await asyncio.sleep(JOB_POLL_S)
        return "timeout"

    async def delete_job(self, namespace: str, name: str) -> None:
        api = await self._batch()
        await api.delete_namespaced_job(name, namespace)
