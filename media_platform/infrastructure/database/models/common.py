"""国标业务表公共审计字段。"""

from sqlalchemy import Column, DateTime, String, func


class BasicAuditColumnsMixin:
    """基础设施表公共审计字段，不包含租户/学校字段。"""

    created_by = Column(
        "cjr", String(64), nullable=False, default="SYSTEM", comment="创建人"
    )
    updated_by = Column(
        "xgr", String(64), nullable=False, default="SYSTEM", comment="修改人"
    )
    created_at = Column(
        "cjsj",
        DateTime(timezone=False),
        nullable=False,
        server_default=func.now(),
        comment="创建时间",
    )
    updated_at = Column(
        "xgsj",
        DateTime(timezone=False),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="修改时间",
    )


class AuditColumnsMixin(BasicAuditColumnsMixin):
    """租户业务表公共审计字段。

    `xxm` 只用于任务、流绑定、媒体文件等需要按业务租户归属的数据表；
    不适用于节点这类系统处理能力实例表。
    """

    school_code = Column("xxm", String(64), nullable=False, comment="学校码")


__all__ = ["AuditColumnsMixin", "BasicAuditColumnsMixin"]
