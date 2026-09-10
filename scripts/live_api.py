import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sync_k8s_secrets import read_env


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["create", "inspect", "cancel", "recording"])
    parser.add_argument("value")
    args = parser.parse_args()
    settings = read_env(Path(".env"))
    with httpx.Client(
        base_url="http://127.0.0.1:8000/v1",
        headers={"X-API-Key": settings["API_KEY"]},
        timeout=30,
    ) as client:
        if args.action == "create":
            response = client.post(
                "/bots",
                json={"meeting_url": args.value, "display_name": "SigmaWave AI Recording Test"},
            )
        elif args.action == "cancel":
            response = client.delete(f"/bots/{args.value}")
        elif args.action == "recording":
            response = client.get(f"/bots/{args.value}/recording")
            response.raise_for_status()
            print(response.headers.get("content-type"), len(response.content))
            if "json" not in response.headers.get("content-type", ""):
                path = Path("research") / f"{args.value}.webm"
                path.write_bytes(response.content)
                print("sha256", hashlib.sha256(response.content).hexdigest())
            else:
                print(json.dumps(response.json()))
            return
        else:
            response = client.get(f"/bots/{args.value}")
            transcript = client.get(f"/bots/{args.value}/transcript")
            transcript.raise_for_status()
            data = transcript.json()
            path = Path("research") / f"{args.value}-transcript.json"
            path.write_text(json.dumps(data, indent=2))
            print("transcript", json.dumps(data))
        print("observed_at", datetime.now(UTC).isoformat(), "http", response.status_code)
        response.raise_for_status()
        if response.content:
            data = response.json()
            data.pop("meeting_url", None)
            print(json.dumps(data))


if __name__ == "__main__":
    main()
