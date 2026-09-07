# Paced joins against the local API. Churn lesson 2026-09-06: ~6 guest-join
# cycles on one meeting code trigger an instant gate — pace (4 min apart),
# rotate names, and move to a fresh meeting link every few joins.

import argparse
import asyncio
import time

import httpx

TERMINAL_STATUSES = {"completed", "failed"}
POLL_INTERVAL_S = 5
DEFAULT_INTERVAL_MIN = 4.0


def _row(bot_id: str, name: str, status: dict, started: float) -> dict:
    return {
        "bot_id": bot_id,
        "name": name,
        "status": status["status"],
        "error_code": status.get("error_code"),
        "seconds": round(time.monotonic() - started),
    }


async def drive_one(
    client: httpx.AsyncClient, url: str, name: str, max_minutes: float, record_seconds: float
) -> dict:
    response = await client.post("/v1/bots", json={"meeting_url": url, "display_name": name})
    response.raise_for_status()
    bot = response.json()
    bot_id = bot["id"]
    started = time.monotonic()
    cancel_sent = False
    while time.monotonic() - started < max_minutes * 60:
        await asyncio.sleep(POLL_INTERVAL_S)
        status = (await client.get(f"/v1/bots/{bot_id}")).json()
        if status["status"] in TERMINAL_STATUSES:
            return _row(bot_id, name, status, started)
        # Free accounts have no "end call for everyone" — the soak ends each
        # cycle through the cooperative cancel once the bot has recorded long
        # enough to carry material.
        if (
            status["status"] == "recording"
            and not cancel_sent
            and time.monotonic() - started >= record_seconds
        ):
            cancel_sent = True
            await client.delete(f"/v1/bots/{bot_id}")
    return _row(bot_id, name, {"status": "poll-timeout", "error_code": None}, started)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--interval-min", type=float, default=DEFAULT_INTERVAL_MIN)
    parser.add_argument("--max-min", type=float, default=50.0)
    parser.add_argument("--record-seconds", type=float, default=60.0)
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--name", default="Fakery Guest")
    args = parser.parse_args()

    results: list[dict] = []
    async with httpx.AsyncClient(base_url=args.api, timeout=30) as client:
        for index in range(1, args.count + 1):
            name = f"{args.name} {index:02d}"
            print(f"[soak] join {index}/{args.count}: {name}", flush=True)
            outcome = await drive_one(client, args.url, name, args.max_min, args.record_seconds)
            results.append(outcome)
            print(f"[soak] {outcome}", flush=True)
            if index < args.count:
                await asyncio.sleep(args.interval_min * 60)

    admitted = sum(1 for r in results if r["status"] == "completed")
    gated = sum(1 for r in results if r.get("error_code") == "join-gated")
    print("[soak] SUMMARY")
    print(f"  joins:     {len(results)}")
    print(f"  completed: {admitted}")
    print(f"  gated:     {gated}")
    for row in results:
        print(
            f"  {row['bot_id']} {row['status']:>12} {str(row.get('error_code')):>24} {row['name']}"
        )


if __name__ == "__main__":
    asyncio.run(main())
