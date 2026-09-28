"""媒体流绑定国标表仓储。"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from media_platform.infrastructure.database.models import MediaStreamBindingModel


class MediaStreamBindingRepository:
    """只读写 `media_stream_binding`，维护 RTC 资源与录制节点的粘性绑定。"""

    def __init__(self, session: Session):
        self.session = session

    def get(self, binding_id: str) -> MediaStreamBindingModel | None:
        """按主键查询绑定。"""

        return self.session.get(MediaStreamBindingModel, binding_id)

    def get_active_by_resource(
        self,
        *,
        school_code: str,
        resource_type: str,
        resource_id: str,
    ) -> MediaStreamBindingModel | None:
        """查询指定资源当前有效绑定。"""

        return self.session.scalar(
            select(MediaStreamBindingModel).where(
                MediaStreamBindingModel.school_code == school_code,
                MediaStreamBindingModel.resource_type == resource_type,
                MediaStreamBindingModel.resource_id == resource_id,
                MediaStreamBindingModel.status == "ACTIVE",
            )
        )

    def get_by_resource(
        self,
        *,
        school_code: str,
        resource_type: str,
        resource_id: str,
    ) -> MediaStreamBindingModel | None:
        """查询资源唯一绑定行，包括已经释放或失败的历史状态。"""

        return self.session.scalar(
            select(MediaStreamBindingModel).where(
                MediaStreamBindingModel.school_code == school_code,
                MediaStreamBindingModel.resource_type == resource_type,
                MediaStreamBindingModel.resource_id == resource_id,
            )
        )

    def get_by_app_stream(
        self,
        *,
        app: str,
        stream_id: str,
    ) -> MediaStreamBindingModel | None:
        """按 ZLMediaKit `app` 和技术流标识查询唯一绑定。"""

        return self.session.scalar(
            select(MediaStreamBindingModel).where(
                MediaStreamBindingModel.app == app,
                MediaStreamBindingModel.stream_id == stream_id,
            )
        )

    def list_active_by_space(
        self,
        *,
        school_code: str,
        space_id: str,
    ) -> list[MediaStreamBindingModel]:
        """查询同一空间下的有效绑定，供摄像头和桌面尽量同节点调度。"""

        return list(
            self.session.scalars(
                select(MediaStreamBindingModel)
                .where(
                    MediaStreamBindingModel.school_code == school_code,
                    MediaStreamBindingModel.space_id == space_id,
                    MediaStreamBindingModel.status == "ACTIVE",
                )
                .order_by(
                    MediaStreamBindingModel.last_active_at.desc(),
                    MediaStreamBindingModel.created_at.desc(),
                )
            )
        )

    def add(self, binding: MediaStreamBindingModel) -> MediaStreamBindingModel:
        """新增绑定并 flush，确保唯一约束错误在当前事务内暴露。"""

        self.session.add(binding)
        self.session.flush()
        return binding

    def reactivate(
        self,
        binding: MediaStreamBindingModel,
        *,
        expected_version: int,
        node_id: str,
        app: str,
        stream_id: str,
        stream_name: str | None,
        stream_mode: str,
        space_id: str | None,
        source_url_ciphertext: str | None,
        last_active_at,
        updated_by: str,
    ) -> bool:
        """按版本条件重新启用已释放/失败绑定，避免并发重复占位。"""

        result = self.session.execute(
            update(MediaStreamBindingModel)
            .where(
                MediaStreamBindingModel.id == binding.id,
                MediaStreamBindingModel.status.in_(("RELEASED", "FAILED")),
                MediaStreamBindingModel.version == expected_version,
            )
            .values(
                node_id=node_id,
                app=app,
                stream_id=stream_id,
                stream_name=stream_name,
                stream_mode=stream_mode,
                space_id=space_id,
                source_url_ciphertext=source_url_ciphertext,
                status="ACTIVE",
                version=expected_version + 1,
                last_active_at=last_active_at,
                updated_by=updated_by,
            )
        )
        return result.rowcount == 1

    def mark_released(
        self,
        binding_id: str,
        *,
        updated_by: str,
        last_active_at,
    ) -> bool:
        """只把 ACTIVE 绑定释放一次，供容量计数同步递减。"""

        result = self.session.execute(
            update(MediaStreamBindingModel)
            .where(
                MediaStreamBindingModel.id == binding_id,
                MediaStreamBindingModel.status == "ACTIVE",
            )
            .values(
                status="RELEASED",
                version=MediaStreamBindingModel.version + 1,
                last_active_at=last_active_at,
                updated_by=updated_by,
            )
        )
        return result.rowcount == 1


__all__ = ["MediaStreamBindingRepository"]
