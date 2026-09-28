from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from media_platform.common.config import get_settings
from media_platform.infrastructure.database.dialect import is_dm
from media_platform.infrastructure.database.driver import ensure_dm_driver
from media_platform.infrastructure.database.sync_async_session import (
    SyncSessionAsyncAdapter,
)
from media_platform.infrastructure.database.url import (
    build_async_db_url,
    build_dm_connect_args,
)

settings = get_settings()
_use_dm = is_dm(settings.DB_TYPE)


@lru_cache(maxsize=1)
def get_engine():
    if _use_dm:
        ensure_dm_driver()
        connect_args = build_dm_connect_args(
            settings.DB_NAME,
            settings.database.dm_mysql_compat,
        )
    else:
        connect_args = {}
    return create_engine(
        settings.DB_URL,
        pool_pre_ping=True,
        pool_size=settings.database.pool_size,
        max_overflow=settings.database.max_overflow,
        connect_args=connect_args,
    )


@lru_cache(maxsize=1)
def get_async_engine():
    if _use_dm:
        return None
    return create_async_engine(
        build_async_db_url(settings.DB_URL, settings.DB_TYPE),
        pool_pre_ping=True,
        pool_size=settings.database.pool_size,
        max_overflow=settings.database.max_overflow,
        connect_args={},
    )


# 兼容旧代码的直接引用
engine = get_engine()
async_engine = get_async_engine()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

AsyncSessionLocal = None
if async_engine is not None:
    AsyncSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=async_engine,
        class_=AsyncSession,
    )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_async_db():
    if _use_dm:
        adapter = SyncSessionAsyncAdapter(SessionLocal())
        try:
            yield adapter
        finally:
            await adapter.close()
    else:
        async with AsyncSessionLocal() as db:
            try:
                yield db
            finally:
                await db.close()
