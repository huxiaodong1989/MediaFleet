"""AI 评课聚合记录与步骤检查点 ORM。"""

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


class ContentEvaluationRecordModel(AuditColumnsMixin, Base):
    """AI 评课聚合记录，保存兼容查询结果和当前恢复位置。"""

    __tablename__ = "content_evaluation"
    __table_args__ = (
        UniqueConstraint("rwzj", name="uk_ai_pkjl_rw"),
        UniqueConstraint("xxm", "ywrwbh", name="uk_ai_pkjl_ywrw"),
        Index("idx_ai_pkjl_zt", "rwzt", "xgsj"),
        {"comment": "AI评课记录表"},
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
        ForeignKey("media_task.zj", name="fk_ai_pkjl_rw"),
        nullable=False,
        comment="公共任务主键",
    )
    business_task_id = Column(
        "ywrwbh",
        String(128),
        nullable=False,
        comment="业务评课任务编号",
    )
    classroom_id = Column(
        "ktbh",
        String(128),
        nullable=False,
        comment="课堂编号",
    )
    status = Column(
        "rwzt",
        String(20),
        nullable=False,
        server_default=text("'pending'"),
        comment="评课状态：pending、processing、completed、failed、cancelled",
    )
    current_step = Column("dqbz", String(64), nullable=True, comment="当前执行步骤代码")
    progress = Column(
        "jd",
        Float,
        nullable=False,
        server_default=text("0"),
        comment="评课进度百分比",
    )
    execution_generation = Column(
        "zxdc",
        Integer,
        nullable=False,
        server_default=text("0"),
        comment="当前执行代次",
    )
    prompt_bundle_id = Column(
        "tsbbzj",
        String(36),
        nullable=True,
        comment="本次任务锁定的提示词版本主键",
    )
    material_digest = Column(
        "clzy",
        String(64),
        nullable=True,
        comment="输入材料SHA-256摘要",
    )
    model_version = Column(
        "mxbb",
        String(128),
        nullable=True,
        comment="本次任务模型版本快照",
    )
    request_snapshot = Column(
        "qqcs",
        portable_json_type(),
        nullable=False,
        comment="已脱敏的评课请求参数JSON",
    )
    result = Column(
        "pjjg",
        portable_json_type(),
        nullable=True,
        comment="八步评课聚合结果JSON",
    )
    error_message = Column("cwxx", Text, nullable=True, comment="评课错误信息")
    preprocessed_subtitle = Column(
        "yclzm",
        Text,
        nullable=True,
        comment="可选的预处理字幕调试快照",
    )


class ContentEvaluationStepModel(AuditColumnsMixin, Base):
    """AI 评课步骤检查点，支持实例崩溃后的跨节点恢复。"""

    __tablename__ = "content_evaluation_step"
    __table_args__ = (
        UniqueConstraint("rwzj", "zxdc", "bzdm", name="uk_ai_pkbz_rw_bz"),
        Index("idx_ai_pkbz_zt", "rwzj", "zxdc", "bzzt"),
        {"comment": "AI评课步骤检查点表"},
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
        ForeignKey("media_task.zj", name="fk_ai_pkbz_rw"),
        nullable=False,
        comment="公共任务主键",
    )
    execution_generation = Column("zxdc", Integer, nullable=False, comment="执行代次")
    step_code = Column("bzdm", String(64), nullable=False, comment="评课步骤代码")
    sort_order = Column("bzxh", Integer, nullable=False, comment="评课步骤顺序")
    status = Column(
        "bzzt",
        String(20),
        nullable=False,
        server_default=text("'pending'"),
        comment="步骤状态：pending、running、completed、failed、skipped",
    )
    input_digest = Column("srzy", String(64), nullable=False, comment="步骤输入SHA-256摘要")
    prompt_bundle_id = Column("tsbbzj", String(36), nullable=False, comment="提示词版本主键")
    model_version = Column("mxbb", String(128), nullable=False, comment="模型版本")
    result = Column(
        "bzjg",
        portable_json_type(),
        nullable=True,
        comment="步骤结构化结果JSON",
    )
    token_usage = Column(
        "lpyl",
        portable_json_type(),
        nullable=True,
        comment="步骤模型令牌用量JSON",
    )
    error_message = Column("cwxx", Text, nullable=True, comment="步骤错误信息")
    started_at = Column("kssj", DateTime(timezone=False), nullable=True, comment="步骤开始时间")
    completed_at = Column("wcsj", DateTime(timezone=False), nullable=True, comment="步骤完成时间")


__all__ = ["ContentEvaluationRecordModel", "ContentEvaluationStepModel"]
