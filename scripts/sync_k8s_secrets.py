import argparse
import base64
import binascii
import json
import subprocess
from pathlib import Path

SECRET_NAME = "twin-secrets"
NAMESPACE = "fakery"
ENV_PATH = Path(".env")
SYNCED_KEYS = {
    "API_KEY",
    "BOT_LOCALE",
    "BOT_RUNTIME",
    "BOT_TIMEZONE",
    "ENV",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_PROVIDER",
    "MEETING_MAX_MINUTES",
    "PROFILE_ENCRYPTION_KEY",
    "RECORD_AUDIO",
    "STT_API_KEY",
    "STT_DIARIZE",
    "STT_LANGUAGE",
    "STT_PROVIDER",
    "WEBHOOK_SIGNING_SECRET",
}
PRODUCTION_REQUIRED_KEYS = {
    "API_KEY",
    "ENV",
    "LLM_API_KEY",
    "LLM_PROVIDER",
    "PROFILE_ENCRYPTION_KEY",
    "STT_API_KEY",
    "STT_PROVIDER",
    "WEBHOOK_SIGNING_SECRET",
}


def read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if key in SYNCED_KEYS and value:
                values[key] = value
    return values


def load_secret() -> dict:
    raw = subprocess.check_output(
        ["kubectl", "-n", NAMESPACE, "get", "secret", SECRET_NAME, "-o", "json"]
    )
    return json.loads(raw)


def apply_secret(secret: dict, values: dict[str, str]) -> None:
    secret.setdefault("data", {}).update(
        {key: base64.b64encode(value.encode()).decode() for key, value in values.items()}
    )
    subprocess.run(
        ["kubectl", "-n", NAMESPACE, "apply", "-f", "-"],
        input=json.dumps(secret).encode(),
        check=True,
        stdout=subprocess.DEVNULL,
    )


def missing_keys(secret: dict) -> list[str]:
    data = secret.get("data", {})
    return sorted(key for key in PRODUCTION_REQUIRED_KEYS if not _has_value(data.get(key)))


def _has_value(encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        return bool(base64.b64decode(encoded, validate=True))
    except (binascii.Error, ValueError):
        return False


def verify_production(secret: dict) -> None:
    missing = missing_keys(secret)
    if missing:
        raise SystemExit("production Secret is missing required keys: " + ", ".join(missing))
    print("verified production Secret:", ", ".join(sorted(PRODUCTION_REQUIRED_KEYS)))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("local", "production"), default="local")
    return parser.parse_args()


def main() -> None:
    secret = load_secret()
    if parse_args().mode == "production":
        verify_production(secret)
        return
    values = read_env(ENV_PATH)
    if not values:
        raise SystemExit(".env has no syncable values")
    apply_secret(secret, values)
    print("synced local settings:", ", ".join(sorted(values)))


if __name__ == "__main__":
    main()
