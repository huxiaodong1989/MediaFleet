"""离线课堂图像分析的视频、时间点与图片持久化事实。"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)

from media_platform.infrastructure.database.base import Base
from media_platform.infrastructure.database.models.common import AuditColumnsMixin
from media_platform.infrastructure.database.types import portable_json_type


def _id_column():
    return Column(
        "zj",
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="主键",
    )


class ImageVideoModel(AuditColumnsMixin, Base):
    """原请求每路视频一行，排除来源仍保留探测和筛选证据。"""

    __tablename__ = "image_analysis_video"
    __table_args__ = (
        UniqueConstraint("rwzj", "sfzy", name="uk_img_video_task_source"),
        UniqueConstraint("zj", "rwzj", "xxm", name="uk_img_video_scope"),
        Index("idx_img_video_selection", "rwzj", "sxzt"),
        Index("idx_img_video_retry", "dqcsj", "dqzt"),
        {"comment": "课堂图像分析视频来源表"},
    )

    id = _id_column()
    task_id = Column(
        "rwzj",
        String(36),
        ForeignKey("media_task.zj", name="fk_img_video_task"),
        nullable=False,
        comment="公共课堂分析任务主键",
    )
    stream_record_id = Column(
        "ljzj", String(128), nullable=False, comment="原请求流媒体记录主键"
    )
    stream_id = Column("lbs", String(128), nullable=False, comment="原请求技术流标识")
    source_identity_hash = Column(
        "sfzy",
        String(64),
        nullable=False,
        comment="原请求流记录主键与技术流标识组合SHA-256",
    )
    source_type = Column("lylx", String(64), nullable=False, comment="镜头来源枚举")
    recording_task_id = Column(
        "lzrwb", String(128), nullable=True, comment="预留关联的录制任务编号"
    )
    ai_config = Column(
        "fxpz",
        portable_json_type(),
        nullable=False,
        comment="该视频请求的行为分析类型JSON",
    )
    source_version = Column(
        "yybb",
        portable_json_type(),
        nullable=True,
        comment="视频对象版本及探测时证据JSON",
    )
    probe_result = Column(
        "tcjg",
        portable_json_type(),
        nullable=True,
        comment="视频时长、PTS和流探测结果JSON",
    )
    time_axis = Column(
        "sjz", portable_json_type(), nullable=True, comment="录像与课堂绝对时间映射JSON"
    )
    selection_status = Column(
        "sxzt",
        String(20),
        nullable=False,
        server_default=text("'PENDING'"),
        comment="来源筛选状态：PENDING、SELECTED、EXCLUDED",
    )
    selection_reason = Column(
        "sxly", String(64), nullable=True, comment="排除或入选原因编码"
    )
    selection_evidence = Column(
        "sxzj",
        portable_json_type(),
        nullable=True,
        comment="覆盖率和时轴可信度证据JSON",
    )
    read_mode = Column(
        "dqfs",
        String(20),
        nullable=False,
        server_default=text("'SEEK'"),
        comment="当前读取模式：SEEK或DOWNLOAD",
    )
    seek_attempts = Column(
        "sqcs",
        Integer,
        nullable=False,
        server_default=text("0"),
        comment="视频级HTTP seek尝试次数",
    )
    download_attempts = Column(
        "xzcs",
        Integer,
        nullable=False,
        server_default=text("0"),
        comment="完整下载回退尝试次数",
    )
    next_retry_at = Column(
        "dqcsj", DateTime(), nullable=True, comment="下次读取重试时间"
    )
    read_status = Column(
        "dqzt",
        String(20),
        nullable=False,
        server_default=text("'PENDING'"),
        comment="视频读取状态：PENDING、ACTIVE、COMPLETED、FAILED",
    )


class ImageSampleModel(AuditColumnsMixin, Base):
    """课堂统一时间点，一次多图推理和一个逻辑通知事件。"""

    __tablename__ = "image_analysis_sample"
    __table_args__ = (
        UniqueConstraint("rwzj", "ktpyhs", name="uk_img_sample_task_offset"),
        UniqueConstraint("zj", "rwzj", "xxm", name="uk_img_sample_scope"),
        UniqueConstraint("sjbh", name="uk_img_sample_event"),
        Index("idx_img_sample_status", "rwzj", "cyzt", "ktpyhs"),
        Index("idx_img_sample_delivery", "tzzt", "tzzysj"),
        {"comment": "课堂图像分析采样时间点表"},
    )

    id = _id_column()
    task_id = Column(
        "rwzj",
        String(36),
        ForeignKey("media_task.zj", name="fk_img_sample_task"),
        nullable=False,
        comment="公共课堂分析任务主键",
    )
    offset_ms = Column(
        "ktpyhs", BigInteger, nullable=False, comment="相对课堂开始时间的采样偏移毫秒"
    )
    sample_time = Column(
        "cysj", DateTime(), nullable=False, comment="采样课堂绝对时间，统一UTC存储"
    )
    expected_video_ids = Column(
        "yqsp",
        portable_json_type(),
        nullable=False,
        comment="冻结的入选视频主键列表JSON",
    )
    requested_metrics = Column(
        "qqzb", portable_json_type(), nullable=False, comment="原请求指标并集JSON"
    )
    unsupported_metrics = Column(
        "bzzb",
        portable_json_type(),
        nullable=False,
        comment="入选来源无法支持的指标JSON",
    )
    status = Column(
        "cyzt",
        String(20),
        nullable=False,
        server_default=text("'PENDING'"),
        comment="采样点状态：PENDING、WAITING、READY、PROCESSING、COMPLETED、UNAVAILABLE、FAILED",
    )
    deadline_at = Column(
        "ddsj", DateTime(), nullable=True, comment="等待本时间点组图齐备截止时间"
    )
    prompt_bundle_id = Column(
        "tsbbzj", String(36), nullable=False, comment="课堂锁定的提示词版本主键"
    )
    prompt_version = Column(
        "tsbbh", Integer, nullable=False, comment="课堂锁定的提示词版本号"
    )
    prompt_hash = Column(
        "tszy", String(64), nullable=False, comment="课堂锁定的提示词内容SHA-256"
    )
    model_snapshot = Column(
        "mxzp",
        portable_json_type(),
        nullable=False,
        comment="锁定的视觉模型及生成参数JSON，不含凭据",
    )
    input_hash = Column(
        "srzy", String(64), nullable=True, comment="组图和指标冻结后的输入SHA-256"
    )
    model_attempts = Column(
        "mxcs",
        Integer,
        nullable=False,
        server_default=text("0"),
        comment="本时间点模型调用尝试次数",
    )
    model_usage = Column(
        "mxyl", portable_json_type(), nullable=True, comment="模型令牌与费用用量JSON"
    )
    result = Column(
        "fxjg",
        portable_json_type(),
        nullable=True,
        comment="本时间点一次联合分析指标结果JSON",
    )
    unavailable_reason = Column(
        "bkyy", String(64), nullable=True, comment="缺图或指标不可用原因编码"
    )
    event_id = Column(
        "sjbh", String(64), nullable=False, comment="组级逻辑通知事件编号"
    )
    notification_payload = Column(
        "tznr",
        portable_json_type(),
        nullable=True,
        comment="已冻结的组级RabbitMQ通知消息JSON",
    )
    notification_status = Column(
        "tzzt",
        String(20),
        nullable=False,
        server_default=text("'PENDING'"),
        comment="通知状态：PENDING、CLAIMED、PUBLISHED、FAILED",
    )
    notification_claim_owner = Column(
        "tzzys", String(128), nullable=True, comment="通知发布领取实例标识"
    )
    notification_claim_until = Column(
        "tzzysj", DateTime(), nullable=True, comment="通知发布领取租约截止时间"
    )
    notification_attempts = Column(
        "tzcs",
        Integer,
        nullable=False,
        server_default=text("0"),
        comment="通知发布尝试次数",
    )


class ImageTaskNotificationModel(AuditColumnsMixin, Base):
    """每个图像任务只有一条终态通知，和采样结果通知分开持久化。"""

    __tablename__ = "image_analysis_task_notification"
    __table_args__ = (
        UniqueConstraint("rwzj", name="uk_img_task_notice_task"),
        UniqueConstraint("sjbh", name="uk_img_task_notice_event"),
        Index("idx_img_task_notice_delivery", "tzzt", "tzzysj"),
        {"comment": "课堂图像分析任务终态通知表"},
    )

    id = _id_column()
    task_id = Column(
        "rwzj",
        String(36),
        ForeignKey("media_task.zj", name="fk_img_task_notice_task"),
        nullable=False,
        comment="公共课堂分析任务主键",
    )
    event_id = Column("sjbh", String(64), nullable=False, comment="任务终态逻辑通知事件编号")
    notification_payload = Column(
        "tznr",
        portable_json_type(),
        nullable=True,
        comment="发布前冻结的任务终态RabbitMQ通知消息JSON",
    )
    notification_status = Column(
        "tzzt",
        String(20),
        nullable=False,
        server_default=text("'WAITING'"),
        comment="通知状态：WAITING、CLAIMED、PUBLISHED、FAILED",
    )
    notification_claim_owner = Column(
        "tzzys", String(128), nullable=True, comment="通知发布领取实例标识"
    )
    notification_claim_until = Column(
        "tzzysj", DateTime(), nullable=True, comment="通知发布领取租约截止时间"
    )
    notification_attempts = Column(
        "tzcs",
        Integer,
        nullable=False,
        server_default=text("0"),
        comment="通知发布尝试次数",
    )


class ImageFrameModel(AuditColumnsMixin, Base):
    """时间点中每个入选视频一张图片，保存抽帧和对象存储检查点。"""

    __tablename__ = "image_analysis_frame"
    __table_args__ = (
        ForeignKeyConstraint(
            ["cydzj", "rwzj", "xxm"],
            [
                "image_analysis_sample.zj",
                "image_analysis_sample.rwzj",
                "image_analysis_sample.xxm",
            ],
            name="fk_img_frame_sample",
        ),
        ForeignKeyConstraint(
            ["spzj", "rwzj", "xxm"],
            [
                "image_analysis_video.zj",
                "image_analysis_video.rwzj",
                "image_analysis_video.xxm",
            ],
            name="fk_img_frame_video",
        ),
        UniqueConstraint("cydzj", "spzj", name="uk_img_frame_sample_video"),
        Index("idx_img_frame_status", "rwzj", "txzt", "dqcsj"),
        {"comment": "课堂图像分析采样图片表"},
    )

    id = _id_column()
    task_id = Column("rwzj", String(36), nullable=False, comment="公共课堂分析任务主键")
    sample_id = Column(
        "cydzj", String(36), nullable=False, comment="课堂采样时间点主键"
    )
    video_id = Column("spzj", String(36), nullable=False, comment="入选视频来源主键")
    target_offset_ms = Column(
        "mbpyhs", BigInteger, nullable=False, comment="目标视频相对起点偏移毫秒"
    )
    actual_pts_ms = Column(
        "sjpyhs", BigInteger, nullable=True, comment="实际抽取画面视频PTS毫秒"
    )
    actual_time = Column(
        "sjsj", DateTime(), nullable=True, comment="实际画面课堂绝对时间，统一UTC存储"
    )
    status = Column(
        "txzt",
        String(20),
        nullable=False,
        server_default=text("'PENDING'"),
        comment="图片状态：PENDING、EXTRACTED、UPLOADED、FAILED、UNAVAILABLE",
    )
    quality = Column(
        "zlxx",
        portable_json_type(),
        nullable=True,
        comment="清晰度、时间偏差及画面质量JSON",
    )
    storage_profile = Column(
        "ccfa", String(64), nullable=True, comment="对象存储受控配置版本标识"
    )
    storage_provider = Column(
        "ccfw", String(32), nullable=True, comment="对象存储类型：COS或MINIO"
    )
    bucket = Column(
        "cttmc", String(255), nullable=True, comment="图片所在对象存储桶名称"
    )
    object_key = Column(
        "dxj", String(1024), nullable=True, comment="稳定对象键，不存短期签名链接"
    )
    content_hash = Column(
        "nrzy", String(64), nullable=True, comment="图片内容SHA-256摘要"
    )
    extract_attempts = Column(
        "tzcs",
        Integer,
        nullable=False,
        server_default=text("0"),
        comment="图片抽帧尝试次数",
    )
    upload_attempts = Column(
        "scscs",
        Integer,
        nullable=False,
        server_default=text("0"),
        comment="图片上传尝试次数",
    )
    next_retry_at = Column(
        "dqcsj", DateTime(), nullable=True, comment="下次图片处理重试时间"
    )
    error_code = Column(
        "cwdm", String(64), nullable=True, comment="图片处理错误代码，不含敏感地址"
    )


__all__ = [
    "ImageVideoModel",
    "ImageSampleModel",
    "ImageTaskNotificationModel",
    "ImageFrameModel",
]
