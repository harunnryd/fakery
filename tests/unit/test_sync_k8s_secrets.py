from pathlib import Path
from runpy import run_path

import pytest

SECRETS = run_path(Path(__file__).parents[2] / "scripts" / "sync_k8s_secrets.py")
missing_keys = SECRETS["missing_keys"]
read_env = SECRETS["read_env"]


def _secret(keys: set[str]) -> dict:
    import base64

    return {"data": {key: base64.b64encode(b"value").decode() for key in keys}}


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("API_KEY=secret\nUNKNOWN=ignored\n", {"API_KEY": "secret"}),
        ("# API_KEY=ignored\nAPI_KEY=\n", {}),
        ("", {}),
    ],
    ids=["allowlist", "empty-and-comment", "empty-file"],
)
def test_read_env_keeps_nonempty_allowlisted_values(
    tmp_path: Path, content: str, expected: dict
) -> None:
    path = tmp_path / ".env"
    path.write_text(content)
    assert read_env(path) == expected


@pytest.mark.parametrize(
    ("present", "expected"),
    [
        (
            {
                "API_KEY",
                "ENV",
                "LLM_API_KEY",
                "LLM_PROVIDER",
                "PROFILE_ENCRYPTION_KEY",
                "STT_API_KEY",
                "STT_PROVIDER",
                "WEBHOOK_SIGNING_SECRET",
            },
            [],
        ),
        (
            {"API_KEY"},
            [
                "ENV",
                "LLM_API_KEY",
                "LLM_PROVIDER",
                "PROFILE_ENCRYPTION_KEY",
                "STT_API_KEY",
                "STT_PROVIDER",
                "WEBHOOK_SIGNING_SECRET",
            ],
        ),
        (
            {
                "API_KEY",
                "ENV",
                "LLM_API_KEY",
                "LLM_PROVIDER",
                "PROFILE_ENCRYPTION_KEY",
                "STT_API_KEY",
                "STT_PROVIDER",
                "WEBHOOK_SIGNING_SECRET",
            },
            ["STT_API_KEY"],
        ),
    ],
    ids=["complete", "missing-required", "invalid-encoding"],
)
def test_missing_keys_reports_production_requirements(
    present: set[str], expected: list[str]
) -> None:
    secret = _secret(present)
    if expected == ["STT_API_KEY"]:
        secret["data"]["STT_API_KEY"] = "not-base64"
    assert missing_keys(secret) == expected
