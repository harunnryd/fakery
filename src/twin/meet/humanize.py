import asyncio
import random
from typing import Any

CLICK_JITTER_PX = 3
MOVE_STEPS = (14, 32)
TYPE_DELAY_MS = (90, 170)
WANDER_ROUNDS = (1, 3)


async def wander(page: Any, rounds: int | None = None) -> None:
    rounds = rounds or random.randint(*WANDER_ROUNDS)
    for _ in range(rounds):
        await page.mouse.move(
            random.uniform(300, 1100), random.uniform(150, 550), steps=random.randint(10, 30)
        )
        await asyncio.sleep(random.uniform(0.2, 0.9))


async def human_click(page: Any, locator: Any) -> None:
    box = await locator.bounding_box()
    if box is None:
        await locator.click(force=True)
        return
    target_x = box["x"] + box["width"] / 2 + random.uniform(-CLICK_JITTER_PX, CLICK_JITTER_PX)
    target_y = box["y"] + box["height"] / 2 + random.uniform(-2, 2)
    await page.mouse.move(target_x, target_y, steps=random.randint(*MOVE_STEPS))
    await asyncio.sleep(random.uniform(0.4, 1.1))
    await page.mouse.down()
    await asyncio.sleep(random.uniform(0.04, 0.12))
    await page.mouse.up()


async def human_type(page: Any, locator: Any, text: str) -> None:
    await human_click(page, locator)
    await asyncio.sleep(random.uniform(0.3, 0.8))
    await page.keyboard.type(text, delay=random.uniform(*TYPE_DELAY_MS))


async def first_visible(page: Any, locators: list[Any]) -> Any | None:
    for locator in locators:
        try:
            if await locator.count() > 0 and await locator.is_visible():
                return locator
        except Exception:
            continue
    return None
