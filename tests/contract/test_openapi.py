import pytest
from fastapi.testclient import TestClient

from twin.main import create_app


@pytest.fixture(scope="module")
def openapi() -> dict:
    client = TestClient(create_app())
    return client.get("/openapi.json").json()


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/healthz", "get"),
        ("/v1/bots", "post"),
        ("/v1/bots/{bot_id}", "get"),
        ("/v1/bots/{bot_id}/transcript", "get"),
    ],
    ids=["healthz", "create_bot", "get_bot", "get_transcript"],
)
def test_openapi_declares_core_paths(openapi: dict, path: str, method: str) -> None:
    assert path in openapi["paths"], f"missing {method.upper()} {path}"
    assert method in openapi["paths"][path]


def test_openapi_targets_openapi_3_1(openapi: dict) -> None:
    assert openapi["openapi"].startswith("3.1")
