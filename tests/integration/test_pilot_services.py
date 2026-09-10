import os

import pytest
from sqlalchemy import text

from twin.storage.database import create_engine_and_sessionmaker


async def test_pilot_database_is_at_expected_revision() -> None:
    url = os.getenv("FAKERY_INTEGRATION_DATABASE_URL")
    if not url:
        pytest.skip("set FAKERY_INTEGRATION_DATABASE_URL for disposable-service integration tests")
    engine, factory = create_engine_and_sessionmaker(url)
    try:
        async with factory() as session:
            result = await session.execute(text("SELECT version_num FROM alembic_version"))
            assert result.scalar_one() == "0009"
    finally:
        await engine.dispose()
