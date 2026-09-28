"""媒体任务表 ORM。"""

import uuid

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)

from media_platform.infrastructure.database.base import Base
from media_platform.infrastructure.database.models.common import AuditColumnsMixin
from media_platform.infrastructure.database.types import portable_json_type


class MediaTaskModel(AuditColumnsMixin, Base):
    """任务事实表，同时保存首期消息领取和发布状态。"""

    __tablename__ = "media_task"
    __table_args__ = (
        UniqueConstraint("xxbh", name="uk_mt_rw_xxbh"),
        UniqueConstraint("xxm", "mdj", name="uk_mt_rw_mdj"),
        UniqueConstraint("xxm", "qqbh", name="uk_mt_rw_qqbh"),
        UniqueConstraint("xxm", "rwlx", "ywrwbh", name="uk_mt_rw_ywrwbh"),
        Index("idx_mt_rw_dd", "rwzt", "yxj", "cjsj"),
        Index("idx_mt_rw_fb", "fbzt", "sdsj"),
        Index("idx_mt_rw_td", "tdqd", "fbzt", "sdsj"),
        Index("idx_mt_rw_zxjd", "zxjdzj", "rwzt"),
        Index(
            "idx_mt_rw_lzyy",
            "lzfwqzj",
            "rwlx",
            "rwzt",
            "yykssj",
            "yyjssj",
        ),
        Index(
            "idx_mt_rw_lzll",
            "yym",
            "lbs",
            "rwzt",
            "yykssj",
            "yyjssj",
        ),
        {"comment": "媒体任务表"},
    )

    id = Column(
        "zj",
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="主键",
    )
    request_id = Column(
        "qqbh", String(64), nullable=True, comment="请求编号，用于接口请求追踪和去重"
    )
    idempotency_key = Column(
        "mdj", String(128), nullable=True, comment="幂等键，同一学校范围内唯一"
    )
    business_task_id = Column(
        "ywrwbh", String(128), nullable=True, comment="业务任务编号，兼容原业务任务ID"
    )
    task_type = Column("rwlx", String(64), nullable=False, comment="任务类型")
    delivery_channel = Column(
        "tdqd",
        String(32),
        nullable=False,
        server_default=text("'MEDIA'"),
        comment="任务投递通道：MEDIA普通媒体、CONTENT_ANALYSIS内容分析",
    )
    routing_key = Column(
        "lyj",
        String(128),
        nullable=False,
        server_default=text("'legacy.migrated'"),
        comment="RabbitMQ任务路由键",
    )
    status = Column(
        "rwzt",
        String(20),
        nullable=False,
        server_default=text("'pending'"),
        comment="任务状态：pending待处理、processing处理中、completed完成、failed失败、cancelled取消",
    )
    priority = Column(
        "yxj", Integer, nullable=False, server_default=text("0"), comment="任务优先级"
    )
    progress = Column(
        "jd", Float, nullable=False, server_default=text("0"), comment="任务进度百分比"
    )
    params = Column(
        "qqcs", portable_json_type(), nullable=False, comment="任务请求参数JSON"
    )
    result = Column(
        "zxjg", portable_json_type(), nullable=True, comment="任务执行结果JSON"
    )
    error_message = Column("cwxx", Text, nullable=True, comment="任务错误信息")
    callback_url = Column("hddz", String(1000), nullable=True, comment="业务回调地址")
    callback_result = Column(
        "hdjg", portable_json_type(), nullable=True, comment="业务回调执行结果JSON"
    )
    executor_node_id = Column(
        "zxjdzj", String(36), nullable=True, comment="执行任务的媒体节点主键"
    )
    recording_server_id = Column(
        "lzfwqzj",
        String(36),
        ForeignKey("recording_server.zj", name="fk_mt_rw_lzfwq"),
        nullable=True,
        comment="录制任务预约所属录制服务器主键",
    )
    recording_app = Column(
        "yym", String(64), nullable=True, comment="录制任务ZLMediaKit应用名"
    )
    recording_stream_id = Column(
        "lbs", String(128), nullable=True, comment="录制任务技术流标识"
    )
    reservation_start_at = Column(
        "yykssj", DateTime(timezone=False), nullable=True, comment="录制容量预约开始时间"
    )
    reservation_end_at = Column(
        "yyjssj",
        DateTime(timezone=False),
        nullable=True,
        comment="录制容量预约结束时间，为空表示开放式录制持续占用",
    )
    retry_count = Column(
        "cs", Integer, nullable=False, server_default=text("0"), comment="当前重试次数"
    )
    max_retries = Column(
        "zdcs", Integer, nullable=False, server_default=text("3"), comment="最大重试次数"
    )
    publish_status = Column(
        "fbzt",
        String(20),
        nullable=False,
        server_default=text("'PENDING'"),
        comment="消息发布状态：PENDING待发布、CLAIMED已领取、PUBLISHED已发布、FAILED失败",
    )
    message_id = Column("xxbh", String(64), nullable=True, comment="RabbitMQ消息编号")
    locked_by = Column("sdslbs", String(128), nullable=True, comment="发布任务锁定实例标识")
    locked_at = Column("sdsj", DateTime(timezone=False), nullable=True, comment="发布时间领取时间")
    published_at = Column("fbsj", DateTime(timezone=False), nullable=True, comment="消息发布时间")
    started_at = Column("kssj", DateTime(timezone=False), nullable=True, comment="任务开始时间")
    completed_at = Column("wcsj", DateTime(timezone=False), nullable=True, comment="任务完成时间")
    execution_generation = Column(
        "zxdc",
        Integer,
        nullable=False,
        server_default=text("0"),
        comment="执行代次，防止旧执行者覆盖新执行者结果",
    )
    lease_owner = Column(
        "zysyd",
        String(128),
        nullable=True,
        comment="执行租约持有者节点编号",
    )
    lease_expires_at = Column(
        "zysxsj",
        DateTime(timezone=False),
        nullable=True,
        comment="执行租约过期时间",
    )


__all__ = ["MediaTaskModel"]
