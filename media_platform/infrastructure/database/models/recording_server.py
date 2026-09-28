"""录制服务器（录制单元）表 ORM。"""

import uuid

from sqlalchemy import Column, ForeignKey, Index, Integer, String, UniqueConstraint, text

from media_platform.infrastructure.database.base import Base
from media_platform.infrastructure.database.models.common import BasicAuditColumnsMixin


class RecordingServerModel(BasicAuditColumnsMixin, Base):
    """一组固定配对的 ZLMediaKit、recorder-node 与录像目录。

    该表保存跨进程重启不变的录制单元身份和运维意图。节点心跳只能更新实际运行
    状态，不能覆盖这里的维护、排空或停用状态。
    """

    __tablename__ = "recording_server"
    __table_args__ = (
        UniqueConstraint("fwqbh", name="uk_mt_fwq_fwqbh"),
        UniqueConstraint("lzjdzj", name="uk_mt_fwq_lzjdzj"),
        UniqueConstraint("zlfwbs", name="uk_mt_fwq_zlfwbs"),
        Index("idx_mt_fwq_zt", "fwqzt"),
        {"comment": "媒体录制服务器表"},
    )

    id = Column(
        "zj",
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="主键",
    )
    server_code = Column(
        "fwqbh", String(64), nullable=False, comment="录制服务器编号"
    )
    server_name = Column(
        "fwqmc", String(128), nullable=False, comment="录制服务器名称"
    )
    status = Column(
        "fwqzt",
        String(20),
        nullable=False,
        server_default=text("'ACTIVE'"),
        comment="服务器状态：ACTIVE启用、DRAINING排空、MAINTENANCE维护、DISABLED停用",
    )
    recorder_node_id = Column(
        "lzjdzj",
        String(36),
        ForeignKey("media_node.zj", name="fk_mt_fwq_lzjd"),
        nullable=False,
        comment="固定关联的录制节点主键",
    )
    zlm_server_id = Column(
        "zlfwbs",
        String(128),
        nullable=False,
        comment="固定关联的ZLMediaKit服务标识",
    )
    zlm_api_url = Column(
        "zljkdz",
        String(500),
        nullable=True,
        comment="ZLMediaKit内部管理接口地址",
    )
    play_host = Column(
        "bfzjdz",
        String(500),
        nullable=True,
        comment="FLV统一播放主机，不含协议、端口和节点路径",
    )
    play_port = Column(
        "bfdk",
        String(16),
        nullable=True,
        comment="FLV播放端口",
    )
    play_protocol = Column(
        "bfxy",
        String(16),
        nullable=True,
        comment="FLV播放协议：http或https",
    )
    rtmp_port = Column(
        "rtmpdk",
        String(16),
        nullable=True,
        comment="ZLMediaKit RTMP端口",
    )
    rtsp_port = Column(
        "rtspdk",
        String(16),
        nullable=True,
        comment="ZLMediaKit RTSP端口",
    )
    record_root = Column(
        "lxgml",
        String(1000),
        nullable=True,
        comment="recorder-node进程内录像根目录",
    )
    max_recordings = Column(
        "zdlzls",
        Integer,
        nullable=False,
        server_default=text("100"),
        comment="最大同时录制路数",
    )
    max_bindings = Column(
        "zdbds",
        Integer,
        nullable=False,
        server_default=text("300"),
        comment="最大流绑定数",
    )
    occupied_bindings = Column(
        "yzbds",
        Integer,
        nullable=False,
        server_default=text("0"),
        comment="已占用流绑定数",
    )


__all__ = ["RecordingServerModel"]
