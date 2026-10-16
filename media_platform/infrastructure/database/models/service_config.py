"""服务运行配置与不可变发布版本。"""

from sqlalchemy import Column, ForeignKey, Integer, String, Text, UniqueConstraint

from media_platform.infrastructure.database.base import Base
from media_platform.infrastructure.database.models.common import AuditColumnsMixin
from media_platform.infrastructure.database.types import portable_json_type


class ServiceConfigModel(AuditColumnsMixin, Base):
    __tablename__ = "service_config"
    __table_args__ = (
        UniqueConstraint("fwbm", "pzfl", "zyfw", "xxm", name="uk_mt_fwpz_fw"),
        {"comment": "服务运行配置当前发布引用"},
    )
    id = Column("zj", String(36), primary_key=True, comment="主键")
    service = Column("fwbm", String(64), nullable=False, comment="服务编码")
    category = Column("pzfl", String(64), nullable=False, comment="配置分类")
    scope = Column(
        "zyfw", String(64), nullable=False, default="GLOBAL", comment="作用范围，首版GLOBAL"
    )
    revision = Column("xdh", Integer, nullable=False, default=0, comment="修订号，用于发布并发控制")
    version_id = Column("dqbbzj", String(36), nullable=True, comment="当前已发布配置版本主键")


class ServiceConfigVersionModel(AuditColumnsMixin, Base):
    __tablename__ = "service_config_version"
    __table_args__ = (
        UniqueConstraint("pzzj", "qqbh", name="uk_mt_fwpzbb_qq"),
        {"comment": "服务运行配置草稿与不可变发布版本"},
    )
    id = Column("zj", String(36), primary_key=True, comment="主键")
    config_id = Column(
        "pzzj",
        String(36),
        ForeignKey("service_config.zj"),
        nullable=False,
        comment="服务配置主键",
    )
    request_id = Column("qqbh", String(64), nullable=False, comment="管理请求幂等编号")
    base_revision = Column("jcxdh", Integer, nullable=False, comment="编辑基于的发布修订号")
    revision = Column("xdh", Integer, nullable=True, comment="修订号，草稿为空")
    status = Column(
        "zt",
        String(16),
        nullable=False,
        default="DRAFT",
        comment="状态：DRAFT草稿、PUBLISHED曾发布",
    )
    content = Column(
        "pznr", portable_json_type(), nullable=False, comment="经类型校验的非敏感配置JSON"
    )
    secret_ciphertext = Column(
        "mymw", Text, nullable=True, comment="密钥密文，使用Fernet加密模型API密钥，禁止返回管理接口"
    )
    prompt_updates = Column(
        "tsgx",
        portable_json_type(),
        nullable=False,
        default=list,
        comment="联合发布的提示词模型更新和基线摘要JSON",
    )
    reason = Column("bgyy", String(512), nullable=False, comment="配置变更原因")
