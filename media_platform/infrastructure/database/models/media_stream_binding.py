"""媒体流粘性绑定表 ORM。"""

import uuid

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)

from media_platform.infrastructure.database.base import Base
from media_platform.infrastructure.database.models.common import AuditColumnsMixin


class MediaStreamBindingModel(AuditColumnsMixin, Base):
    """摄像头或桌面终端与媒体节点之间的持久化绑定。"""

    __tablename__ = "media_stream_binding"
    __table_args__ = (
        UniqueConstraint("xxm", "zylx", "zybh", name="uk_mt_lb_zy"),
        UniqueConstraint("yym", "lbs", name="uk_mt_lb_lbs"),
        Index("idx_mt_lb_jd", "jdzj", "bdzt"),
        {"comment": "媒体流绑定表"},
    )

    id = Column(
        "zj",
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="主键",
    )
    resource_type = Column(
        "zylx",
        String(20),
        nullable=False,
        comment="资源类型：CAMERA摄像头、DESKTOP桌面终端",
    )
    resource_id = Column(
        "zybh",
        String(64),
        nullable=False,
        comment="内部兼容键，由app和技术流标识生成，不对外提供",
    )
    space_id = Column("kjbh", String(64), nullable=True, comment="空间或教室编号")
    node_id = Column(
        "jdzj",
        String(36),
        ForeignKey("media_node.zj", name="fk_mt_lb_jd"),
        nullable=False,
        comment="媒体节点主键",
    )
    app = Column(
        "yym",
        String(64),
        nullable=False,
        server_default=text("'live'"),
        comment="ZLMediaKit应用名",
    )
    stream_id = Column(
        "lbs",
        String(128),
        nullable=False,
        comment="技术流标识，由RTC首次创建并在重试或迁移时复用",
    )
    stream_name = Column("lmc", String(255), nullable=True, comment="业务展示流名称")
    stream_mode = Column(
        "llx", String(20), nullable=False, comment="流类型：PULL拉流、PUSH推流"
    )
    status = Column(
        "bdzt",
        String(20),
        nullable=False,
        server_default=text("'ACTIVE'"),
        comment="绑定状态：ACTIVE有效、MIGRATING迁移中、RELEASED已释放、FAILED失败",
    )
    version = Column(
        "bbyh", BigInteger, nullable=False, server_default=text("0"), comment="绑定版本号"
    )
    source_url_ciphertext = Column(
        "yldz", Text, nullable=True, comment="加密后的源流地址，仅拉流模式使用"
    )
    last_active_at = Column(
        "zhhysj", DateTime(timezone=False), nullable=True, comment="最后活跃时间"
    )


__all__ = ["MediaStreamBindingModel"]
