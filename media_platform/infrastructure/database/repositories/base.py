from typing import Generic, TypeVar, Type, Optional, List, Dict, Any, Union
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete
from sqlalchemy.orm import selectinload

from media_platform.infrastructure.database.base import Base

ModelType = TypeVar("ModelType", bound=Base)
CreateSchemaType = TypeVar("CreateSchemaType", bound=BaseModel)
UpdateSchemaType = TypeVar("UpdateSchemaType", bound=BaseModel)

class BaseRepository(Generic[ModelType, CreateSchemaType, UpdateSchemaType]):
    """
    基础仓库类，提供通用的数据库操作方法
    """

    def __init__(self, model: Type[ModelType]):
        self.model = model

    async def create(self, db: AsyncSession, obj_in: Union[CreateSchemaType, Dict[str, Any]]) -> ModelType:
        """
        创建一个新记录
        """
        if isinstance(obj_in, dict):
            obj_in_data = obj_in
        else:
            obj_in_data = obj_in.dict()

        db_obj = self.model(**obj_in_data)
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def get(self, db: AsyncSession, id: Any) -> Optional[ModelType]:
        """
        通过ID获取记录
        """
        stmt = select(self.model).where(self.model.id == id)
        result = await db.execute(stmt)
        return result.scalars().first()

    async def get_by_field(self, db: AsyncSession, field: str, value: Any) -> Optional[ModelType]:
        """
        通过指定字段获取记录
        """
        stmt = select(self.model).where(getattr(self.model, field) == value)
        result = await db.execute(stmt)
        return result.scalars().first()

    async def get_multi(
        self, db: AsyncSession, *, skip: int = 0, limit: int = 100
    ) -> List[ModelType]:
        """
        获取多条记录
        """
        stmt = select(self.model).offset(skip).limit(limit)
        result = await db.execute(stmt)
        return result.scalars().all()

    async def update(
        self, db: AsyncSession, *, db_obj: ModelType, obj_in: Union[UpdateSchemaType, Dict[str, Any]]
    ) -> ModelType:
        """
        更新记录
        """
        obj_data = db_obj.__dict__
        if isinstance(obj_in, dict):
            update_data = obj_in
        else:
            update_data = obj_in.dict(exclude_unset=True)

        for field in obj_data:
            if field in update_data:
                setattr(db_obj, field, update_data[field])

        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def update_by_id(
        self, db: AsyncSession, *, id: Any, obj_in: Union[UpdateSchemaType, Dict[str, Any]]
    ) -> Optional[ModelType]:
        """
        通过ID更新记录
        """
        db_obj = await self.get(db, id)
        if db_obj:
            return await self.update(db, db_obj=db_obj, obj_in=obj_in)
        return None

    async def update_by_field(
        self, db: AsyncSession, *, field: str, value: Any, obj_in: Union[UpdateSchemaType, Dict[str, Any]]
    ) -> Optional[ModelType]:
        """
        通过指定字段更新记录
        """
        db_obj = await self.get_by_field(db, field, value)
        if db_obj:
            return await self.update(db, db_obj=db_obj, obj_in=obj_in)
        return None

    async def remove(self, db: AsyncSession, *, id: Any) -> Optional[ModelType]:
        """
        删除记录
        """
        db_obj = await self.get(db, id)
        if db_obj:
            await db.delete(db_obj)
            await db.commit()
            return db_obj
        return None
