"""Add MediaFleet image analysis video, sample and frame checkpoints.

Revision ID: 20261009_0002
Revises: 20260928_0001
"""

from alembic import op
import sqlalchemy as sa


revision = "20261009_0002"
down_revision = "20260928_0001"
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
    ]


def upgrade() -> None:
    op.create_table(
        "image_analysis_video",
        sa.Column("zj", sa.String(36), nullable=False, comment="主键"),
        sa.Column(
            "rwzj", sa.String(36), nullable=False, comment="公共课堂分析任务主键"
        ),
        sa.Column(
            "ljzj", sa.String(128), nullable=False, comment="原请求流媒体记录主键"
        ),
        sa.Column("lbs", sa.String(128), nullable=False, comment="原请求技术流标识"),
        sa.Column(
            "sfzy",
            sa.String(64),
            nullable=False,
            comment="原请求流记录主键与技术流标识组合SHA-256",
        ),
        sa.Column("lylx", sa.String(64), nullable=False, comment="镜头来源枚举"),
        sa.Column(
            "lzrwb", sa.String(128), nullable=True, comment="预留关联的录制任务编号"
        ),
        sa.Column(
            "fxpz", _json_type(), nullable=False, comment="该视频请求的行为分析类型JSON"
        ),
        sa.Column(
            "yybb", _json_type(), nullable=True, comment="视频对象版本及探测时证据JSON"
        ),
        sa.Column(
            "tcjg", _json_type(), nullable=True, comment="视频时长、PTS和流探测结果JSON"
        ),
        sa.Column(
            "sjz", _json_type(), nullable=True, comment="录像与课堂绝对时间映射JSON"
        ),
        sa.Column(
            "sxzt",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'PENDING'"),
            comment="来源筛选状态：PENDING、SELECTED、EXCLUDED",
        ),
        sa.Column("sxly", sa.String(64), nullable=True, comment="排除或入选原因编码"),
        sa.Column(
            "sxzj", _json_type(), nullable=True, comment="覆盖率和时轴可信度证据JSON"
        ),
        sa.Column(
            "dqfs",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'SEEK'"),
            comment="当前读取模式：SEEK或DOWNLOAD",
        ),
        sa.Column(
            "sqcs",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="视频级HTTP seek尝试次数",
        ),
        sa.Column(
            "xzcs",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="完整下载回退尝试次数",
        ),
        sa.Column("dqcsj", sa.DateTime(), nullable=True, comment="下次读取重试时间"),
        sa.Column(
            "dqzt",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'PENDING'"),
            comment="视频读取状态：PENDING、ACTIVE、COMPLETED、FAILED",
        ),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("zj", name="pk_ai_txsp"),
        sa.ForeignKeyConstraint(["rwzj"], ["media_task.zj"], name="fk_img_video_task"),
        sa.UniqueConstraint("rwzj", "sfzy", name="uk_img_video_task_source"),
        sa.UniqueConstraint("zj", "rwzj", "xxm", name="uk_img_video_scope"),
        comment="课堂图像分析视频来源表",
        **MYSQL_TABLE_OPTIONS,
    )
    op.create_index("idx_img_video_selection", "image_analysis_video", ["rwzj", "sxzt"])
    op.create_index("idx_img_video_retry", "image_analysis_video", ["dqcsj", "dqzt"])

    op.create_table(
        "image_analysis_sample",
        sa.Column("zj", sa.String(36), nullable=False, comment="主键"),
        sa.Column(
            "rwzj", sa.String(36), nullable=False, comment="公共课堂分析任务主键"
        ),
        sa.Column(
            "ktpyhs",
            sa.BigInteger(),
            nullable=False,
            comment="相对课堂开始时间的采样偏移毫秒",
        ),
        sa.Column(
            "cysj",
            sa.DateTime(),
            nullable=False,
            comment="采样课堂绝对时间，统一UTC存储",
        ),
        sa.Column(
            "yqsp", _json_type(), nullable=False, comment="冻结的入选视频主键列表JSON"
        ),
        sa.Column("qqzb", _json_type(), nullable=False, comment="原请求指标并集JSON"),
        sa.Column(
            "bzzb", _json_type(), nullable=False, comment="入选来源无法支持的指标JSON"
        ),
        sa.Column(
            "cyzt",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'PENDING'"),
            comment="采样点状态：PENDING、WAITING、READY、PROCESSING、COMPLETED、UNAVAILABLE、FAILED",
        ),
        sa.Column(
            "ddsj", sa.DateTime(), nullable=True, comment="等待本时间点组图齐备截止时间"
        ),
        sa.Column(
            "tsbbzj", sa.String(36), nullable=False, comment="课堂锁定的提示词版本主键"
        ),
        sa.Column(
            "tsbbh", sa.Integer(), nullable=False, comment="课堂锁定的提示词版本号"
        ),
        sa.Column(
            "tszy", sa.String(64), nullable=False, comment="课堂锁定的提示词内容SHA-256"
        ),
        sa.Column(
            "mxzp",
            _json_type(),
            nullable=False,
            comment="锁定的视觉模型及生成参数JSON，不含凭据",
        ),
        sa.Column(
            "srzy",
            sa.String(64),
            nullable=True,
            comment="组图和指标冻结后的输入SHA-256",
        ),
        sa.Column(
            "mxcs",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="本时间点模型调用尝试次数",
        ),
        sa.Column(
            "mxyl", _json_type(), nullable=True, comment="模型令牌与费用用量JSON"
        ),
        sa.Column(
            "fxjg",
            _json_type(),
            nullable=True,
            comment="本时间点一次联合分析指标结果JSON",
        ),
        sa.Column(
            "bkyy", sa.String(64), nullable=True, comment="缺图或指标不可用原因编码"
        ),
        sa.Column(
            "sjbh", sa.String(64), nullable=False, comment="组级逻辑通知事件编号"
        ),
        sa.Column(
            "tznr",
            _json_type(),
            nullable=True,
            comment="已冻结的组级RabbitMQ通知消息JSON",
        ),
        sa.Column(
            "tzzt",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'PENDING'"),
            comment="通知状态：PENDING、CLAIMED、PUBLISHED、FAILED",
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
        *_audit_columns(),
        sa.PrimaryKeyConstraint("zj", name="pk_ai_txcyd"),
        sa.ForeignKeyConstraint(["rwzj"], ["media_task.zj"], name="fk_img_sample_task"),
        sa.UniqueConstraint("rwzj", "ktpyhs", name="uk_img_sample_task_offset"),
        sa.UniqueConstraint("zj", "rwzj", "xxm", name="uk_img_sample_scope"),
        sa.UniqueConstraint("sjbh", name="uk_img_sample_event"),
        comment="课堂图像分析采样时间点表",
        **MYSQL_TABLE_OPTIONS,
    )
    op.create_index("idx_img_sample_status", "image_analysis_sample", ["rwzj", "cyzt", "ktpyhs"])
    op.create_index("idx_img_sample_delivery", "image_analysis_sample", ["tzzt", "tzzysj"])

    op.create_table(
        "image_analysis_frame",
        sa.Column("zj", sa.String(36), nullable=False, comment="主键"),
        sa.Column(
            "rwzj", sa.String(36), nullable=False, comment="公共课堂分析任务主键"
        ),
        sa.Column("cydzj", sa.String(36), nullable=False, comment="课堂采样时间点主键"),
        sa.Column("spzj", sa.String(36), nullable=False, comment="入选视频来源主键"),
        sa.Column(
            "mbpyhs",
            sa.BigInteger(),
            nullable=False,
            comment="目标视频相对起点偏移毫秒",
        ),
        sa.Column(
            "sjpyhs", sa.BigInteger(), nullable=True, comment="实际抽取画面视频PTS毫秒"
        ),
        sa.Column(
            "sjsj",
            sa.DateTime(),
            nullable=True,
            comment="实际画面课堂绝对时间，统一UTC存储",
        ),
        sa.Column(
            "txzt",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'PENDING'"),
            comment="图片状态：PENDING、EXTRACTED、UPLOADED、FAILED、UNAVAILABLE",
        ),
        sa.Column(
            "zlxx",
            _json_type(),
            nullable=True,
            comment="清晰度、时间偏差及画面质量JSON",
        ),
        sa.Column(
            "ccfa", sa.String(64), nullable=True, comment="对象存储受控配置版本标识"
        ),
        sa.Column(
            "ccfw", sa.String(32), nullable=True, comment="对象存储类型：COS或MINIO"
        ),
        sa.Column(
            "cttmc", sa.String(255), nullable=True, comment="图片所在对象存储桶名称"
        ),
        sa.Column(
            "dxj",
            sa.String(1024),
            nullable=True,
            comment="稳定对象键，不存短期签名链接",
        ),
        sa.Column("nrzy", sa.String(64), nullable=True, comment="图片内容SHA-256摘要"),
        sa.Column(
            "tzcs",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="图片抽帧尝试次数",
        ),
        sa.Column(
            "scscs",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="图片上传尝试次数",
        ),
        sa.Column(
            "dqcsj", sa.DateTime(), nullable=True, comment="下次图片处理重试时间"
        ),
        sa.Column(
            "cwdm",
            sa.String(64),
            nullable=True,
            comment="图片处理错误代码，不含敏感地址",
        ),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("zj", name="pk_ai_txz"),
        sa.ForeignKeyConstraint(
            ["cydzj", "rwzj", "xxm"],
            ["image_analysis_sample.zj", "image_analysis_sample.rwzj", "image_analysis_sample.xxm"],
            name="fk_img_frame_sample",
        ),
        sa.ForeignKeyConstraint(
            ["spzj", "rwzj", "xxm"],
            ["image_analysis_video.zj", "image_analysis_video.rwzj", "image_analysis_video.xxm"],
            name="fk_img_frame_video",
        ),
        sa.UniqueConstraint("cydzj", "spzj", name="uk_img_frame_sample_video"),
        comment="课堂图像分析采样图片表",
        **MYSQL_TABLE_OPTIONS,
    )
    op.create_index("idx_img_frame_status", "image_analysis_frame", ["rwzj", "txzt", "dqcsj"])

    if op.get_bind().dialect.name == "mysql":
        op.execute("ALTER TABLE prompt_version COMMENT = 'AI分析提示词版本表'")
        op.alter_column(
            "prompt_version",
            "tsnr",
            existing_type=sa.JSON(),
            existing_nullable=False,
            comment="完整提示词方案JSON，包含提示词、模型和生成参数",
        )


def downgrade() -> None:
    op.drop_index("idx_img_frame_status", table_name="image_analysis_frame")
    op.drop_table("image_analysis_frame")
    op.drop_index("idx_img_sample_delivery", table_name="image_analysis_sample")
    op.drop_index("idx_img_sample_status", table_name="image_analysis_sample")
    op.drop_table("image_analysis_sample")
    op.drop_index("idx_img_video_retry", table_name="image_analysis_video")
    op.drop_index("idx_img_video_selection", table_name="image_analysis_video")
    op.drop_table("image_analysis_video")
    if op.get_bind().dialect.name == "mysql":
        op.execute("ALTER TABLE prompt_version COMMENT = 'AI评课提示词版本表'")
        op.alter_column(
            "prompt_version",
            "tsnr",
            existing_type=sa.JSON(),
            existing_nullable=False,
            comment="完整提示词方案JSON，包含步骤提示词、模型和生成参数",
        )
