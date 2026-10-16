"""调用方身份、范围化凭据和请求审计 ORM。"""

from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text

from media_platform.infrastructure.database.base import Base
from media_platform.infrastructure.database.models.common import BasicAuditColumnsMixin
from media_platform.infrastructure.database.types import portable_json_type


class ApiPrincipalModel(BasicAuditColumnsMixin, Base):
    """稳定调用方身份；凭据轮换不会改变该身份。"""

    __tablename__ = "api_principal"
    __table_args__ = (
        Index("idx_api_principal_status", "zt"),
        {"comment": "API调用方身份"},
    )

    id = Column("zj", String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    caller_id = Column("dybs", String(64), nullable=False, unique=True, comment="调用方标识")
    display_name = Column("dymc", String(128), nullable=False, comment="调用方名称")
    status = Column("zt", String(20), nullable=False, default="ACTIVE", comment="状态")


class ApiCredentialModel(BasicAuditColumnsMixin, Base):
    """API Key 元数据；数据库只保存派生摘要和随机盐。"""

    __tablename__ = "api_credential"
    __table_args__ = (
        Index("idx_api_credential_principal", "dzzj", "zt"),
        Index("idx_api_credential_expires", "zt", "gqsj"),
        {"comment": "范围化API凭据"},
    )

    id = Column("zj", String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    principal_id = Column(
        "dzzj",
        String(36),
        ForeignKey("api_principal.zj", name="fk_api_credential_principal"),
        nullable=False,
    )
    name = Column("pzmc", String(128), nullable=False, comment="凭据名称")
    secret_salt = Column("mysy", String(64), nullable=False, comment="密钥派生随机盐")
    secret_digest = Column("myzy", String(128), nullable=False, comment="密钥派生摘要")
    secret_iterations = Column(
        "mycs", Integer, nullable=False, default=310000, comment="PBKDF2迭代次数"
    )
    request_key_digest = Column(
        "qqmdj",
        String(64),
        nullable=True,
        unique=True,
        comment="创建请求幂等键SHA-256",
    )
    scopes = Column("qxfw", portable_json_type(), nullable=False, comment="权限范围JSON")
    status = Column("zt", String(20), nullable=False, default="ACTIVE", comment="状态")
    expires_at = Column("gqsj", DateTime(timezone=False), nullable=True, comment="过期时间")
    last_used_at = Column("zhsy", DateTime(timezone=False), nullable=True, comment="最后使用时间")
    rotated_at = Column("lzhgsj", DateTime(timezone=False), nullable=True, comment="最近轮换时间")
    revoked_at = Column("cxsj", DateTime(timezone=False), nullable=True, comment="吊销时间")


class ApiAuditEventModel(Base):
    """受保护写请求的不可变身份与结果审计记录。"""

    __tablename__ = "api_audit_event"
    __table_args__ = (
        Index("idx_api_audit_principal_time", "dybs", "kssj"),
        Index("idx_api_audit_request", "qqbh"),
        Index("idx_api_audit_result_time", "jg", "kssj"),
        {"comment": "API写请求审计事件"},
    )

    id = Column("zj", String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    request_id = Column("qqbh", String(64), nullable=False, comment="请求追踪编号")
    principal_id = Column("dzzj", String(36), nullable=True, comment="调用方身份主键")
    credential_id = Column("pzzj", String(36), nullable=True, comment="凭据主键")
    caller_id = Column("dybs", String(64), nullable=False, comment="调用方标识")
    method = Column("qqff", String(16), nullable=False, comment="HTTP方法")
    path = Column("qqlj", String(512), nullable=False, comment="不含查询参数的请求路径")
    action = Column("cz", String(128), nullable=False, comment="稳定操作标识")
    required_scopes = Column("qxfw", portable_json_type(), nullable=False, comment="所需范围JSON")
    idempotency_key = Column("mdj", String(128), nullable=True, comment="幂等键摘要")
    result = Column("jg", String(20), nullable=False, default="STARTED", comment="执行结果")
    http_status = Column("httpzt", Integer, nullable=True, comment="HTTP状态码")
    started_at = Column("kssj", DateTime(timezone=False), nullable=False, comment="开始时间")
    completed_at = Column("wcsj", DateTime(timezone=False), nullable=True, comment="完成时间")
    error_code = Column("cwmb", String(64), nullable=True, comment="错误类别")
    error_message = Column("cwxx", Text, nullable=True, comment="脱敏错误摘要")


__all__ = ["ApiAuditEventModel", "ApiCredentialModel", "ApiPrincipalModel"]
