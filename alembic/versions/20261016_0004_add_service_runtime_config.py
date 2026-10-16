"""服务运行配置版本；仅DDL，不从环境变量导入业务密钥。"""

from alembic import op
import sqlalchemy as sa

revision = "20261016_0004"
down_revision = "20261012_0003"
branch_labels = None
depends_on = None


def _audit():
    return [
        sa.Column("cjr", sa.String(64), nullable=False, comment="创建人"),
        sa.Column("xgr", sa.String(64), nullable=False, comment="修改人"),
        sa.Column("xxm", sa.String(64), nullable=False, comment="学校码，首版GLOBAL"),
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


def upgrade():
    options = dict(
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_table(
        "service_config",
        sa.Column("zj", sa.String(36), primary_key=True, comment="主键"),
        sa.Column("fwbm", sa.String(64), nullable=False, comment="服务编码"),
        sa.Column("pzfl", sa.String(64), nullable=False, comment="配置分类"),
        sa.Column("zyfw", sa.String(64), nullable=False, comment="作用范围，首版GLOBAL"),
        sa.Column("xdh", sa.Integer(), nullable=False, comment="修订号，用于发布并发控制"),
        sa.Column("dqbbzj", sa.String(36), nullable=True, comment="当前已发布配置版本主键"),
        *_audit(),
        sa.UniqueConstraint("fwbm", "pzfl", "zyfw", "xxm", name="uk_mt_fwpz_fw"),
        comment="服务运行配置当前发布引用",
        **options,
    )
    json_type = sa.JSON().with_variant(sa.Text(), "dm")
    op.create_table(
        "service_config_version",
        sa.Column("zj", sa.String(36), primary_key=True, comment="主键"),
        sa.Column("pzzj", sa.String(36), nullable=False, comment="服务配置主键"),
        sa.Column("qqbh", sa.String(64), nullable=False, comment="管理请求幂等编号"),
        sa.Column("jcxdh", sa.Integer(), nullable=False, comment="编辑基于的发布修订号"),
        sa.Column("xdh", sa.Integer(), nullable=True, comment="修订号，草稿为空"),
        sa.Column(
            "zt", sa.String(16), nullable=False, comment="状态：DRAFT草稿、PUBLISHED曾发布"
        ),
        sa.Column("pznr", json_type, nullable=False, comment="经类型校验的非敏感配置JSON"),
        sa.Column(
            "mymw", sa.Text(), nullable=True, comment="密钥密文，使用Fernet加密模型API密钥，禁止返回管理接口"
        ),
        sa.Column("tsgx", json_type, nullable=False, comment="联合发布的提示词模型更新和基线摘要JSON"),
        sa.Column("bgyy", sa.String(512), nullable=False, comment="配置变更原因"),
        *_audit(),
        sa.ForeignKeyConstraint(["pzzj"], ["service_config.zj"], name="fk_mt_fwpzbb_pz"),
        sa.UniqueConstraint("pzzj", "qqbh", name="uk_mt_fwpzbb_qq"),
        comment="服务运行配置草稿与不可变发布版本",
        **options,
    )


def downgrade():
    op.drop_table("service_config_version")
    op.drop_table("service_config")
