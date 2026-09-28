"""媒体节点表 ORM。"""

import uuid

from sqlalchemy import Column, DateTime, Index, Integer, String, UniqueConstraint, text

from media_platform.infrastructure.database.base import Base
from media_platform.infrastructure.database.models.common import BasicAuditColumnsMixin
from media_platform.infrastructure.database.types import portable_json_type


class MediaNodeModel(BasicAuditColumnsMixin, Base):
    """录制节点和通用媒体 Worker 的持久化身份与容量配置。"""

    __tablename__ = "media_node"
    __table_args__ = (
        UniqueConstraint("jdbh", name="uk_mt_jd_jdbh"),
        Index("idx_mt_jd_zt_xt", "jdzt", "zhxjsj"),
        {"comment": "媒体节点表"},
    )

    id = Column(
        "zj",
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="主键",
    )
    node_code = Column("jdbh", String(64), nullable=False, comment="节点编号")
    node_name = Column("jdmc", String(128), nullable=False, comment="节点名称")
    node_type = Column(
        "jdlx",
        String(32),
        nullable=False,
        comment="节点类型：RECORDER录制节点、WORKER通用媒体节点",
    )
    status = Column(
        "jdzt",
        String(20),
        nullable=False,
        server_default=text("'OFFLINE'"),
        comment="节点状态：ONLINE在线、OFFLINE离线、DRAINING排空、DISABLED停用",
    )
    agent_url = Column("dlfwdz", String(500), nullable=True, comment="节点代理服务地址")
    zlm_api_url = Column(
        "zljkdz", String(500), nullable=True, comment="ZLMediaKit内部管理接口地址"
    )
    zlm_server_id = Column(
        "zlfwbs", String(128), nullable=True, comment="ZLMediaKit服务标识"
    )
    record_root = Column(
        "lxgml", String(1000), nullable=True, comment="节点本地录像根目录"
    )
    weight = Column(
        "qz", Integer, nullable=False, server_default=text("100"), comment="调度权重"
    )
    capabilities = Column(
        "gnlb",
        portable_json_type(),
        nullable=True,
        comment="节点能力列表JSON，包含可处理的任务类型和协议",
    )
    capacity_config = Column(
        "nlpz",
        portable_json_type(),
        nullable=True,
        comment="节点容量配置JSON，包含录像数、磁盘和并发硬阈值",
    )
    readiness_status = Column(
        "jxzt",
        String(20),
        nullable=False,
        server_default=text("'NOT_READY'"),
        comment="节点就绪状态：READY就绪、NOT_READY未就绪",
    )
    readiness_details = Column(
        "jxmx",
        portable_json_type(),
        nullable=True,
        comment="节点依赖就绪检查明细JSON",
    )
    last_heartbeat_at = Column(
        "zhxjsj", DateTime(timezone=False), nullable=True, comment="最后心跳时间"
    )


__all__ = ["MediaNodeModel"]
