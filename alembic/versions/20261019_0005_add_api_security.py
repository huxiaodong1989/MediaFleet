"""Add scoped API credentials and trusted request audit.

Revision ID: 20261019_0005
Revises: 20261016_0004
"""

from alembic import op
import sqlalchemy as sa


revision = "20261019_0005"
down_revision = "20261016_0004"
branch_labels = None
depends_on = None

MYSQL_TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def _json_type():
    return sa.JSON().with_variant(sa.Text(), "dm")


def _audit_columns():
    return [
        sa.Column("cjr", sa.String(64), nullable=False, comment="创建人"),
        sa.Column("xgr", sa.String(64), nullable=False, comment="修改人"),
        sa.Column(
            "cjsj",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="创建时间",
        ),
        sa.Column(
            "xgsj",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="修改时间",
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "api_principal",
        sa.Column("zj", sa.String(36), nullable=False),
        sa.Column("dybs", sa.String(64), nullable=False, comment="调用方标识"),
        sa.Column("dymc", sa.String(128), nullable=False, comment="调用方名称"),
        sa.Column("zt", sa.String(20), nullable=False, comment="状态"),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("zj", name="pk_api_principal"),
        sa.UniqueConstraint("dybs", name="uk_api_principal_caller"),
        **MYSQL_TABLE_OPTIONS,
    )
    op.create_index("idx_api_principal_status", "api_principal", ["zt"])

    op.create_table(
        "api_credential",
        sa.Column("zj", sa.String(36), nullable=False),
        sa.Column("dzzj", sa.String(36), nullable=False, comment="调用方身份主键"),
        sa.Column("pzmc", sa.String(128), nullable=False, comment="凭据名称"),
        sa.Column("mysy", sa.String(64), nullable=False, comment="密钥派生随机盐"),
        sa.Column("myzy", sa.String(128), nullable=False, comment="密钥派生摘要"),
        sa.Column("mycs", sa.Integer(), nullable=False, comment="PBKDF2迭代次数"),
        sa.Column("qqmdj", sa.String(64), nullable=True, comment="创建请求幂等键SHA-256"),
        sa.Column("qxfw", _json_type(), nullable=False, comment="权限范围JSON"),
        sa.Column("zt", sa.String(20), nullable=False, comment="状态"),
        sa.Column("gqsj", sa.DateTime(), nullable=True, comment="过期时间"),
        sa.Column("zhsy", sa.DateTime(), nullable=True, comment="最后使用时间"),
        sa.Column("lzhgsj", sa.DateTime(), nullable=True, comment="最近轮换时间"),
        sa.Column("cxsj", sa.DateTime(), nullable=True, comment="吊销时间"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(
            ["dzzj"], ["api_principal.zj"], name="fk_api_credential_principal"
        ),
        sa.PrimaryKeyConstraint("zj", name="pk_api_credential"),
        sa.UniqueConstraint("qqmdj", name="uk_api_credential_request_key"),
        **MYSQL_TABLE_OPTIONS,
    )
    op.create_index(
        "idx_api_credential_principal", "api_credential", ["dzzj", "zt"]
    )
    op.create_index(
        "idx_api_credential_expires", "api_credential", ["zt", "gqsj"]
    )

    op.create_table(
        "api_audit_event",
        sa.Column("zj", sa.String(36), nullable=False),
        sa.Column("qqbh", sa.String(64), nullable=False, comment="请求追踪编号"),
        sa.Column("dzzj", sa.String(36), nullable=True, comment="调用方身份主键"),
        sa.Column("pzzj", sa.String(36), nullable=True, comment="凭据主键"),
        sa.Column("dybs", sa.String(64), nullable=False, comment="调用方标识"),
        sa.Column("qqff", sa.String(16), nullable=False, comment="HTTP方法"),
        sa.Column("qqlj", sa.String(512), nullable=False, comment="请求路径"),
        sa.Column("cz", sa.String(128), nullable=False, comment="稳定操作标识"),
        sa.Column("qxfw", _json_type(), nullable=False, comment="所需范围JSON"),
        sa.Column("mdj", sa.String(128), nullable=True, comment="幂等键摘要"),
        sa.Column("jg", sa.String(20), nullable=False, comment="执行结果"),
        sa.Column("httpzt", sa.Integer(), nullable=True, comment="HTTP状态码"),
        sa.Column("kssj", sa.DateTime(), nullable=False, comment="开始时间"),
        sa.Column("wcsj", sa.DateTime(), nullable=True, comment="完成时间"),
        sa.Column("cwmb", sa.String(64), nullable=True, comment="错误类别"),
        sa.Column("cwxx", sa.Text(), nullable=True, comment="脱敏错误摘要"),
        sa.PrimaryKeyConstraint("zj", name="pk_api_audit_event"),
        **MYSQL_TABLE_OPTIONS,
    )
    op.create_index(
        "idx_api_audit_principal_time", "api_audit_event", ["dybs", "kssj"]
    )
    op.create_index("idx_api_audit_request", "api_audit_event", ["qqbh"])
    op.create_index(
        "idx_api_audit_result_time", "api_audit_event", ["jg", "kssj"]
    )


def downgrade() -> None:
    op.drop_index("idx_api_audit_result_time", table_name="api_audit_event")
    op.drop_index("idx_api_audit_request", table_name="api_audit_event")
    op.drop_index("idx_api_audit_principal_time", table_name="api_audit_event")
    op.drop_table("api_audit_event")
    op.drop_index("idx_api_credential_expires", table_name="api_credential")
    op.drop_index("idx_api_credential_principal", table_name="api_credential")
    op.drop_table("api_credential")
    op.drop_index("idx_api_principal_status", table_name="api_principal")
    op.drop_table("api_principal")
