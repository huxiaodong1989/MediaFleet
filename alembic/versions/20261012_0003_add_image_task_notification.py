"""Add durable MediaFleet image task terminal notifications.

Revision ID: 20261012_0003
Revises: 20261009_0002
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "20261012_0003"
down_revision = "20261009_0002"
branch_labels = None
depends_on = None

MYSQL_TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def _media_task_id_type():
    """跟随现有任务主键字符定义，避免外键字符集或排序规则不兼容。"""
    bind = op.get_bind()
    if bind.dialect.name != "mysql" or op.get_context().as_sql:
        return sa.String(36)
    row = bind.execute(
        sa.text(
            "SELECT DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, "
            "CHARACTER_SET_NAME, COLLATION_NAME "
            "FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = 'media_task' AND COLUMN_NAME = 'zj'"
        )
    ).mappings().one_or_none()
    if row is None or row["DATA_TYPE"].lower() not in {"char", "varchar"}:
        raise RuntimeError("media_task.zj 必须是可用的CHAR/VARCHAR主键")
    return mysql.VARCHAR(
        length=int(row["CHARACTER_MAXIMUM_LENGTH"]),
        charset=row["CHARACTER_SET_NAME"],
        collation=row["COLLATION_NAME"],
    )


def _json_type():
    return sa.JSON().with_variant(sa.Text(), "dm")


def upgrade() -> None:
    op.create_table(
        "image_analysis_task_notification",
        sa.Column("zj", sa.String(36), nullable=False, comment="主键"),
        sa.Column(
            "rwzj",
            _media_task_id_type(),
            nullable=False,
            comment="公共课堂分析任务主键",
        ),
        sa.Column(
            "sjbh", sa.String(64), nullable=False, comment="任务终态逻辑通知事件编号"
        ),
        sa.Column(
            "tznr",
            _json_type(),
            nullable=True,
            comment="发布前冻结的任务终态RabbitMQ通知消息JSON",
        ),
        sa.Column(
            "tzzt",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'WAITING'"),
            comment="通知状态：WAITING、CLAIMED、PUBLISHED、FAILED",
        ),
        sa.Column(
            "tzzys", sa.String(128), nullable=True, comment="通知发布领取实例标识"
        ),
        sa.Column(
            "tzzysj", sa.DateTime(), nullable=True, comment="通知发布领取租约截止时间"
        ),
        sa.Column(
            "tzcs",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="通知发布尝试次数",
        ),
        sa.Column("cjr", sa.String(64), nullable=False, comment="创建人"),
        sa.Column("xgr", sa.String(64), nullable=False, comment="修改人"),
        sa.Column("xxm", sa.String(64), nullable=False, comment="学校码"),
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
        sa.PrimaryKeyConstraint("zj", name="pk_img_task_notice"),
        sa.ForeignKeyConstraint(
            ["rwzj"], ["media_task.zj"], name="fk_img_task_notice_task"
        ),
        sa.UniqueConstraint("rwzj", name="uk_img_task_notice_task"),
        sa.UniqueConstraint("sjbh", name="uk_img_task_notice_event"),
        comment="课堂图像分析任务终态通知表",
        **MYSQL_TABLE_OPTIONS,
    )
    op.create_index(
        "idx_img_task_notice_delivery",
        "image_analysis_task_notification",
        ["tzzt", "tzzysj"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_img_task_notice_delivery",
        table_name="image_analysis_task_notification",
    )
    op.drop_table("image_analysis_task_notification")
