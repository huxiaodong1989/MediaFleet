"""AI 评课提示词整套版本 ORM，供调用中心和内容分析实例共享。"""

import uuid

from sqlalchemy import Column, DateTime, Index, Integer, String, UniqueConstraint, text

from media_platform.infrastructure.database.base import Base
from media_platform.infrastructure.database.models.common import AuditColumnsMixin
from media_platform.infrastructure.database.types import portable_json_type


class ContentPromptBundleModel(AuditColumnsMixin, Base):
    """整套评课提示词的不可变版本。"""

    __tablename__ = "prompt_version"
    __table_args__ = (
        UniqueConstraint("xxm", "fabm", "bbh", name="uk_ai_tsbb_fa_bb"),
        Index("idx_ai_tsbb_qy", "xxm", "fabm", "zt", "bbh"),
        {"comment": "AI评课提示词版本表"},
    )

    id = Column("zj", String(36), primary_key=True, default=lambda: str(uuid.uuid4()), comment="主键")
    scheme_code = Column("fabm", String(64), nullable=False, comment="提示词方案编码")
    version = Column("bbh", Integer, nullable=False, comment="提示词方案版本号")
    status = Column(
        "zt",
        String(20),
        nullable=False,
        server_default=text("'DRAFT'"),
        comment="版本状态：DRAFT草稿、PUBLISHED已发布、ARCHIVED已归档",
    )
    content = Column(
        "tsnr",
        portable_json_type(),
        nullable=False,
        comment="完整提示词方案JSON，包含步骤提示词、模型和生成参数",
    )
    content_hash = Column("nrzy", String(64), nullable=False, comment="提示词方案SHA-256摘要")
    published_at = Column("fbsj", DateTime(timezone=False), nullable=True, comment="版本发布时间")


__all__ = ["ContentPromptBundleModel"]
