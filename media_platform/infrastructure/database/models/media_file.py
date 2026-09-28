"""媒体文件表 ORM。"""

import uuid

from sqlalchemy import BigInteger, Column, ForeignKey, Index, String

from media_platform.infrastructure.database.base import Base
from media_platform.infrastructure.database.models.common import AuditColumnsMixin
from media_platform.infrastructure.database.types import portable_json_type


class MediaFileModel(AuditColumnsMixin, Base):
    """任务产生的视频、音频、封面、字幕等媒体产物。"""

    __tablename__ = "media_artifact"
    __table_args__ = (
        Index("idx_mt_wj_rw", "rwzj"),
        Index("idx_mt_wj_lx", "xxm", "wjlx"),
        {"comment": "媒体文件表"},
    )

    id = Column(
        "zj",
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="主键",
    )
    task_id = Column(
        "rwzj",
        String(36),
        ForeignKey("media_task.zj", name="fk_mt_wj_rw", ondelete="CASCADE"),
        nullable=False,
        comment="关联媒体任务主键",
    )
    file_type = Column(
        "wjlx",
        String(32),
        nullable=False,
        comment="文件类型：VIDEO视频、AUDIO音频、COVER封面、SUBTITLE字幕、OTHER其他",
    )
    file_name = Column("wjmc", String(255), nullable=False, comment="文件名称")
    file_url = Column("wjdz", String(1000), nullable=False, comment="文件访问地址")
    relative_path = Column("xdlj", String(1000), nullable=True, comment="存储相对路径")
    bucket_name = Column("cttmc", String(128), nullable=True, comment="对象存储桶名称")
    file_size = Column("wjdx", BigInteger, nullable=False, comment="文件大小，单位字节")
    mime_type = Column("mllx", String(128), nullable=False, comment="文件MIME类型")
    extra_info = Column(
        "kzxx",
        portable_json_type(),
        nullable=True,
        comment="文件扩展信息JSON，包含时长、分辨率、校验值等元数据",
    )


__all__ = ["MediaFileModel"]
