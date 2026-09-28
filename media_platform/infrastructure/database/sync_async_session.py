"""将达梦等无 async 驱动的同步 Session 适配为 async 接口。"""

import asyncio
from typing import Any


class SyncSessionAsyncAdapter:
    """包装同步 Session，提供与 AsyncSession 兼容的 async 方法。"""

    def __init__(self, session):
        self._session = session
        self.sync_session = session

    def add(self, instance: Any) -> None:
        self._session.add(instance)

    async def execute(self, statement, params=None, **kwargs):
        if params is not None:
            return await asyncio.to_thread(
                self._session.execute, statement, params, **kwargs
            )
        return await asyncio.to_thread(self._session.execute, statement, **kwargs)

    async def commit(self) -> None:
        await asyncio.to_thread(self._session.commit)

    async def rollback(self) -> None:
        await asyncio.to_thread(self._session.rollback)

    async def refresh(self, instance, attribute_names=None) -> None:
        if attribute_names is not None:
            await asyncio.to_thread(
                self._session.refresh, instance, attribute_names=attribute_names
            )
        else:
            await asyncio.to_thread(self._session.refresh, instance)

    async def delete(self, instance) -> None:
        await asyncio.to_thread(self._session.delete, instance)

    async def close(self) -> None:
        await asyncio.to_thread(self._session.close)
