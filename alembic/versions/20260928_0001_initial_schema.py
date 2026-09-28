"""initial schema

Revision ID: 20260928_0001
Revises:
Create Date: 2026-09-28 10:57:46.675878
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from media_platform.infrastructure.database.types import PortableJSON


revision: str = '20260928_0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MYSQL_TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def upgrade() -> None:
    op.create_table('media_node',
    sa.Column('zj', sa.String(length=36), nullable=False, comment='主键'),
    sa.Column('jdbh', sa.String(length=64), nullable=False, comment='节点编号'),
    sa.Column('jdmc', sa.String(length=128), nullable=False, comment='节点名称'),
    sa.Column('jdlx', sa.String(length=32), nullable=False, comment='节点类型：RECORDER录制节点、WORKER通用媒体节点'),
    sa.Column('jdzt', sa.String(length=20), server_default=sa.text("'OFFLINE'"), nullable=False, comment='节点状态：ONLINE在线、OFFLINE离线、DRAINING排空、DISABLED停用'),
    sa.Column('dlfwdz', sa.String(length=500), nullable=True, comment='节点代理服务地址'),
    sa.Column('zljkdz', sa.String(length=500), nullable=True, comment='ZLMediaKit内部管理接口地址'),
    sa.Column('zlfwbs', sa.String(length=128), nullable=True, comment='ZLMediaKit服务标识'),
    sa.Column('lxgml', sa.String(length=1000), nullable=True, comment='节点本地录像根目录'),
    sa.Column('qz', sa.Integer(), server_default=sa.text('100'), nullable=False, comment='调度权重'),
    sa.Column('gnlb', PortableJSON(), nullable=True, comment='节点能力列表JSON，包含可处理的任务类型和协议'),
    sa.Column('nlpz', PortableJSON(), nullable=True, comment='节点容量配置JSON，包含录像数、磁盘和并发硬阈值'),
    sa.Column('jxzt', sa.String(length=20), server_default=sa.text("'NOT_READY'"), nullable=False, comment='节点就绪状态：READY就绪、NOT_READY未就绪'),
    sa.Column('jxmx', PortableJSON(), nullable=True, comment='节点依赖就绪检查明细JSON'),
    sa.Column('zhxjsj', sa.DateTime(), nullable=True, comment='最后心跳时间'),
    sa.Column('cjr', sa.String(length=64), nullable=False, comment='创建人'),
    sa.Column('xgr', sa.String(length=64), nullable=False, comment='修改人'),
    sa.Column('cjsj', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='创建时间'),
    sa.Column('xgsj', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='修改时间'),
    sa.PrimaryKeyConstraint('zj', name=op.f('pk_media_node')),
    sa.UniqueConstraint('jdbh', name='uk_mt_jd_jdbh'),
    comment='媒体节点表',
    **MYSQL_TABLE_OPTIONS,
    )
    op.create_index('idx_mt_jd_zt_xt', 'media_node', ['jdzt', 'zhxjsj'], unique=False)
    op.create_table('prompt_version',
    sa.Column('zj', sa.String(length=36), nullable=False, comment='主键'),
    sa.Column('fabm', sa.String(length=64), nullable=False, comment='提示词方案编码'),
    sa.Column('bbh', sa.Integer(), nullable=False, comment='提示词方案版本号'),
    sa.Column('zt', sa.String(length=20), server_default=sa.text("'DRAFT'"), nullable=False, comment='版本状态：DRAFT草稿、PUBLISHED已发布、ARCHIVED已归档'),
    sa.Column('tsnr', PortableJSON(), nullable=False, comment='完整提示词方案JSON，包含步骤提示词、模型和生成参数'),
    sa.Column('nrzy', sa.String(length=64), nullable=False, comment='提示词方案SHA-256摘要'),
    sa.Column('fbsj', sa.DateTime(), nullable=True, comment='版本发布时间'),
    sa.Column('xxm', sa.String(length=64), nullable=False, comment='学校码'),
    sa.Column('cjr', sa.String(length=64), nullable=False, comment='创建人'),
    sa.Column('xgr', sa.String(length=64), nullable=False, comment='修改人'),
    sa.Column('cjsj', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='创建时间'),
    sa.Column('xgsj', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='修改时间'),
    sa.PrimaryKeyConstraint('zj', name=op.f('pk_prompt_version')),
    sa.UniqueConstraint('xxm', 'fabm', 'bbh', name='uk_ai_tsbb_fa_bb'),
    comment='AI评课提示词版本表',
    **MYSQL_TABLE_OPTIONS,
    )
    op.create_index('idx_ai_tsbb_qy', 'prompt_version', ['xxm', 'fabm', 'zt', 'bbh'], unique=False)
    op.create_table('media_stream_binding',
    sa.Column('zj', sa.String(length=36), nullable=False, comment='主键'),
    sa.Column('zylx', sa.String(length=20), nullable=False, comment='资源类型：CAMERA摄像头、DESKTOP桌面终端'),
    sa.Column('zybh', sa.String(length=64), nullable=False, comment='内部兼容键，由app和技术流标识生成，不对外提供'),
    sa.Column('kjbh', sa.String(length=64), nullable=True, comment='空间或教室编号'),
    sa.Column('jdzj', sa.String(length=36), nullable=False, comment='媒体节点主键'),
    sa.Column('yym', sa.String(length=64), server_default=sa.text("'live'"), nullable=False, comment='ZLMediaKit应用名'),
    sa.Column('lbs', sa.String(length=128), nullable=False, comment='技术流标识，由RTC首次创建并在重试或迁移时复用'),
    sa.Column('lmc', sa.String(length=255), nullable=True, comment='业务展示流名称'),
    sa.Column('llx', sa.String(length=20), nullable=False, comment='流类型：PULL拉流、PUSH推流'),
    sa.Column('bdzt', sa.String(length=20), server_default=sa.text("'ACTIVE'"), nullable=False, comment='绑定状态：ACTIVE有效、MIGRATING迁移中、RELEASED已释放、FAILED失败'),
    sa.Column('bbyh', sa.BigInteger(), server_default=sa.text('0'), nullable=False, comment='绑定版本号'),
    sa.Column('yldz', sa.Text(), nullable=True, comment='加密后的源流地址，仅拉流模式使用'),
    sa.Column('zhhysj', sa.DateTime(), nullable=True, comment='最后活跃时间'),
    sa.Column('xxm', sa.String(length=64), nullable=False, comment='学校码'),
    sa.Column('cjr', sa.String(length=64), nullable=False, comment='创建人'),
    sa.Column('xgr', sa.String(length=64), nullable=False, comment='修改人'),
    sa.Column('cjsj', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='创建时间'),
    sa.Column('xgsj', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='修改时间'),
    sa.ForeignKeyConstraint(['jdzj'], ['media_node.zj'], name='fk_mt_lb_jd'),
    sa.PrimaryKeyConstraint('zj', name=op.f('pk_media_stream_binding')),
    sa.UniqueConstraint('xxm', 'zylx', 'zybh', name='uk_mt_lb_zy'),
    sa.UniqueConstraint('yym', 'lbs', name='uk_mt_lb_lbs'),
    comment='媒体流绑定表',
    **MYSQL_TABLE_OPTIONS,
    )
    op.create_index('idx_mt_lb_jd', 'media_stream_binding', ['jdzj', 'bdzt'], unique=False)
    op.create_table('recording_server',
    sa.Column('zj', sa.String(length=36), nullable=False, comment='主键'),
    sa.Column('fwqbh', sa.String(length=64), nullable=False, comment='录制服务器编号'),
    sa.Column('fwqmc', sa.String(length=128), nullable=False, comment='录制服务器名称'),
    sa.Column('fwqzt', sa.String(length=20), server_default=sa.text("'ACTIVE'"), nullable=False, comment='服务器状态：ACTIVE启用、DRAINING排空、MAINTENANCE维护、DISABLED停用'),
    sa.Column('lzjdzj', sa.String(length=36), nullable=False, comment='固定关联的录制节点主键'),
    sa.Column('zlfwbs', sa.String(length=128), nullable=False, comment='固定关联的ZLMediaKit服务标识'),
    sa.Column('zljkdz', sa.String(length=500), nullable=True, comment='ZLMediaKit内部管理接口地址'),
    sa.Column('bfzjdz', sa.String(length=500), nullable=True, comment='FLV统一播放主机，不含协议、端口和节点路径'),
    sa.Column('bfdk', sa.String(length=16), nullable=True, comment='FLV播放端口'),
    sa.Column('bfxy', sa.String(length=16), nullable=True, comment='FLV播放协议：http或https'),
    sa.Column('rtmpdk', sa.String(length=16), nullable=True, comment='ZLMediaKit RTMP端口'),
    sa.Column('rtspdk', sa.String(length=16), nullable=True, comment='ZLMediaKit RTSP端口'),
    sa.Column('lxgml', sa.String(length=1000), nullable=True, comment='recorder-node进程内录像根目录'),
    sa.Column('zdlzls', sa.Integer(), server_default=sa.text('100'), nullable=False, comment='最大同时录制路数'),
    sa.Column('zdbds', sa.Integer(), server_default=sa.text('300'), nullable=False, comment='最大流绑定数'),
    sa.Column('yzbds', sa.Integer(), server_default=sa.text('0'), nullable=False, comment='已占用流绑定数'),
    sa.Column('cjr', sa.String(length=64), nullable=False, comment='创建人'),
    sa.Column('xgr', sa.String(length=64), nullable=False, comment='修改人'),
    sa.Column('cjsj', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='创建时间'),
    sa.Column('xgsj', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='修改时间'),
    sa.ForeignKeyConstraint(['lzjdzj'], ['media_node.zj'], name='fk_mt_fwq_lzjd'),
    sa.PrimaryKeyConstraint('zj', name=op.f('pk_recording_server')),
    sa.UniqueConstraint('fwqbh', name='uk_mt_fwq_fwqbh'),
    sa.UniqueConstraint('lzjdzj', name='uk_mt_fwq_lzjdzj'),
    sa.UniqueConstraint('zlfwbs', name='uk_mt_fwq_zlfwbs'),
    comment='媒体录制服务器表',
    **MYSQL_TABLE_OPTIONS,
    )
    op.create_index('idx_mt_fwq_zt', 'recording_server', ['fwqzt'], unique=False)
    op.create_table('media_task',
    sa.Column('zj', sa.String(length=36), nullable=False, comment='主键'),
    sa.Column('qqbh', sa.String(length=64), nullable=True, comment='请求编号，用于接口请求追踪和去重'),
    sa.Column('mdj', sa.String(length=128), nullable=True, comment='幂等键，同一学校范围内唯一'),
    sa.Column('ywrwbh', sa.String(length=128), nullable=True, comment='业务任务编号，兼容原业务任务ID'),
    sa.Column('rwlx', sa.String(length=64), nullable=False, comment='任务类型'),
    sa.Column('tdqd', sa.String(length=32), server_default=sa.text("'MEDIA'"), nullable=False, comment='任务投递通道：MEDIA普通媒体、CONTENT_ANALYSIS内容分析'),
    sa.Column('lyj', sa.String(length=128), server_default=sa.text("'legacy.migrated'"), nullable=False, comment='RabbitMQ任务路由键'),
    sa.Column('rwzt', sa.String(length=20), server_default=sa.text("'pending'"), nullable=False, comment='任务状态：pending待处理、processing处理中、completed完成、failed失败、cancelled取消'),
    sa.Column('yxj', sa.Integer(), server_default=sa.text('0'), nullable=False, comment='任务优先级'),
    sa.Column('jd', sa.Float(), server_default=sa.text('0'), nullable=False, comment='任务进度百分比'),
    sa.Column('qqcs', PortableJSON(), nullable=False, comment='任务请求参数JSON'),
    sa.Column('zxjg', PortableJSON(), nullable=True, comment='任务执行结果JSON'),
    sa.Column('cwxx', sa.Text(), nullable=True, comment='任务错误信息'),
    sa.Column('hddz', sa.String(length=1000), nullable=True, comment='业务回调地址'),
    sa.Column('hdjg', PortableJSON(), nullable=True, comment='业务回调执行结果JSON'),
    sa.Column('zxjdzj', sa.String(length=36), nullable=True, comment='执行任务的媒体节点主键'),
    sa.Column('lzfwqzj', sa.String(length=36), nullable=True, comment='录制任务预约所属录制服务器主键'),
    sa.Column('yym', sa.String(length=64), nullable=True, comment='录制任务ZLMediaKit应用名'),
    sa.Column('lbs', sa.String(length=128), nullable=True, comment='录制任务技术流标识'),
    sa.Column('yykssj', sa.DateTime(), nullable=True, comment='录制容量预约开始时间'),
    sa.Column('yyjssj', sa.DateTime(), nullable=True, comment='录制容量预约结束时间，为空表示开放式录制持续占用'),
    sa.Column('cs', sa.Integer(), server_default=sa.text('0'), nullable=False, comment='当前重试次数'),
    sa.Column('zdcs', sa.Integer(), server_default=sa.text('3'), nullable=False, comment='最大重试次数'),
    sa.Column('fbzt', sa.String(length=20), server_default=sa.text("'PENDING'"), nullable=False, comment='消息发布状态：PENDING待发布、CLAIMED已领取、PUBLISHED已发布、FAILED失败'),
    sa.Column('xxbh', sa.String(length=64), nullable=True, comment='RabbitMQ消息编号'),
    sa.Column('sdslbs', sa.String(length=128), nullable=True, comment='发布任务锁定实例标识'),
    sa.Column('sdsj', sa.DateTime(), nullable=True, comment='发布时间领取时间'),
    sa.Column('fbsj', sa.DateTime(), nullable=True, comment='消息发布时间'),
    sa.Column('kssj', sa.DateTime(), nullable=True, comment='任务开始时间'),
    sa.Column('wcsj', sa.DateTime(), nullable=True, comment='任务完成时间'),
    sa.Column('zxdc', sa.Integer(), server_default=sa.text('0'), nullable=False, comment='执行代次，防止旧执行者覆盖新执行者结果'),
    sa.Column('zysyd', sa.String(length=128), nullable=True, comment='执行租约持有者节点编号'),
    sa.Column('zysxsj', sa.DateTime(), nullable=True, comment='执行租约过期时间'),
    sa.Column('xxm', sa.String(length=64), nullable=False, comment='学校码'),
    sa.Column('cjr', sa.String(length=64), nullable=False, comment='创建人'),
    sa.Column('xgr', sa.String(length=64), nullable=False, comment='修改人'),
    sa.Column('cjsj', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='创建时间'),
    sa.Column('xgsj', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='修改时间'),
    sa.ForeignKeyConstraint(['lzfwqzj'], ['recording_server.zj'], name='fk_mt_rw_lzfwq'),
    sa.PrimaryKeyConstraint('zj', name=op.f('pk_media_task')),
    sa.UniqueConstraint('xxbh', name='uk_mt_rw_xxbh'),
    sa.UniqueConstraint('xxm', 'mdj', name='uk_mt_rw_mdj'),
    sa.UniqueConstraint('xxm', 'qqbh', name='uk_mt_rw_qqbh'),
    sa.UniqueConstraint('xxm', 'rwlx', 'ywrwbh', name='uk_mt_rw_ywrwbh'),
    comment='媒体任务表',
    **MYSQL_TABLE_OPTIONS,
    )
    op.create_index('idx_mt_rw_dd', 'media_task', ['rwzt', 'yxj', 'cjsj'], unique=False)
    op.create_index('idx_mt_rw_fb', 'media_task', ['fbzt', 'sdsj'], unique=False)
    op.create_index('idx_mt_rw_lzll', 'media_task', ['yym', 'lbs', 'rwzt', 'yykssj', 'yyjssj'], unique=False)
    op.create_index('idx_mt_rw_lzyy', 'media_task', ['lzfwqzj', 'rwlx', 'rwzt', 'yykssj', 'yyjssj'], unique=False)
    op.create_index('idx_mt_rw_td', 'media_task', ['tdqd', 'fbzt', 'sdsj'], unique=False)
    op.create_index('idx_mt_rw_zxjd', 'media_task', ['zxjdzj', 'rwzt'], unique=False)
    op.create_table('content_evaluation',
    sa.Column('zj', sa.String(length=36), nullable=False, comment='主键'),
    sa.Column('rwzj', sa.String(length=36), nullable=False, comment='公共任务主键'),
    sa.Column('ywrwbh', sa.String(length=128), nullable=False, comment='业务评课任务编号'),
    sa.Column('ktbh', sa.String(length=128), nullable=False, comment='课堂编号'),
    sa.Column('rwzt', sa.String(length=20), server_default=sa.text("'pending'"), nullable=False, comment='评课状态：pending、processing、completed、failed、cancelled'),
    sa.Column('dqbz', sa.String(length=64), nullable=True, comment='当前执行步骤代码'),
    sa.Column('jd', sa.Float(), server_default=sa.text('0'), nullable=False, comment='评课进度百分比'),
    sa.Column('zxdc', sa.Integer(), server_default=sa.text('0'), nullable=False, comment='当前执行代次'),
    sa.Column('tsbbzj', sa.String(length=36), nullable=True, comment='本次任务锁定的提示词版本主键'),
    sa.Column('clzy', sa.String(length=64), nullable=True, comment='输入材料SHA-256摘要'),
    sa.Column('mxbb', sa.String(length=128), nullable=True, comment='本次任务模型版本快照'),
    sa.Column('qqcs', PortableJSON(), nullable=False, comment='已脱敏的评课请求参数JSON'),
    sa.Column('pjjg', PortableJSON(), nullable=True, comment='八步评课聚合结果JSON'),
    sa.Column('cwxx', sa.Text(), nullable=True, comment='评课错误信息'),
    sa.Column('yclzm', sa.Text(), nullable=True, comment='可选的预处理字幕调试快照'),
    sa.Column('xxm', sa.String(length=64), nullable=False, comment='学校码'),
    sa.Column('cjr', sa.String(length=64), nullable=False, comment='创建人'),
    sa.Column('xgr', sa.String(length=64), nullable=False, comment='修改人'),
    sa.Column('cjsj', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='创建时间'),
    sa.Column('xgsj', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='修改时间'),
    sa.ForeignKeyConstraint(['rwzj'], ['media_task.zj'], name='fk_ai_pkjl_rw'),
    sa.PrimaryKeyConstraint('zj', name=op.f('pk_content_evaluation')),
    sa.UniqueConstraint('rwzj', name='uk_ai_pkjl_rw'),
    sa.UniqueConstraint('xxm', 'ywrwbh', name='uk_ai_pkjl_ywrw'),
    comment='AI评课记录表',
    **MYSQL_TABLE_OPTIONS,
    )
    op.create_index('idx_ai_pkjl_zt', 'content_evaluation', ['rwzt', 'xgsj'], unique=False)
    op.create_table('content_evaluation_step',
    sa.Column('zj', sa.String(length=36), nullable=False, comment='主键'),
    sa.Column('rwzj', sa.String(length=36), nullable=False, comment='公共任务主键'),
    sa.Column('zxdc', sa.Integer(), nullable=False, comment='执行代次'),
    sa.Column('bzdm', sa.String(length=64), nullable=False, comment='评课步骤代码'),
    sa.Column('bzxh', sa.Integer(), nullable=False, comment='评课步骤顺序'),
    sa.Column('bzzt', sa.String(length=20), server_default=sa.text("'pending'"), nullable=False, comment='步骤状态：pending、running、completed、failed、skipped'),
    sa.Column('srzy', sa.String(length=64), nullable=False, comment='步骤输入SHA-256摘要'),
    sa.Column('tsbbzj', sa.String(length=36), nullable=False, comment='提示词版本主键'),
    sa.Column('mxbb', sa.String(length=128), nullable=False, comment='模型版本'),
    sa.Column('bzjg', PortableJSON(), nullable=True, comment='步骤结构化结果JSON'),
    sa.Column('lpyl', PortableJSON(), nullable=True, comment='步骤模型令牌用量JSON'),
    sa.Column('cwxx', sa.Text(), nullable=True, comment='步骤错误信息'),
    sa.Column('kssj', sa.DateTime(), nullable=True, comment='步骤开始时间'),
    sa.Column('wcsj', sa.DateTime(), nullable=True, comment='步骤完成时间'),
    sa.Column('xxm', sa.String(length=64), nullable=False, comment='学校码'),
    sa.Column('cjr', sa.String(length=64), nullable=False, comment='创建人'),
    sa.Column('xgr', sa.String(length=64), nullable=False, comment='修改人'),
    sa.Column('cjsj', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='创建时间'),
    sa.Column('xgsj', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='修改时间'),
    sa.ForeignKeyConstraint(['rwzj'], ['media_task.zj'], name='fk_ai_pkbz_rw'),
    sa.PrimaryKeyConstraint('zj', name=op.f('pk_content_evaluation_step')),
    sa.UniqueConstraint('rwzj', 'zxdc', 'bzdm', name='uk_ai_pkbz_rw_bz'),
    comment='AI评课步骤检查点表',
    **MYSQL_TABLE_OPTIONS,
    )
    op.create_index('idx_ai_pkbz_zt', 'content_evaluation_step', ['rwzj', 'zxdc', 'bzzt'], unique=False)
    op.create_table('media_artifact',
    sa.Column('zj', sa.String(length=36), nullable=False, comment='主键'),
    sa.Column('rwzj', sa.String(length=36), nullable=False, comment='关联媒体任务主键'),
    sa.Column('wjlx', sa.String(length=32), nullable=False, comment='文件类型：VIDEO视频、AUDIO音频、COVER封面、SUBTITLE字幕、OTHER其他'),
    sa.Column('wjmc', sa.String(length=255), nullable=False, comment='文件名称'),
    sa.Column('wjdz', sa.String(length=1000), nullable=False, comment='文件访问地址'),
    sa.Column('xdlj', sa.String(length=1000), nullable=True, comment='存储相对路径'),
    sa.Column('cttmc', sa.String(length=128), nullable=True, comment='对象存储桶名称'),
    sa.Column('wjdx', sa.BigInteger(), nullable=False, comment='文件大小，单位字节'),
    sa.Column('mllx', sa.String(length=128), nullable=False, comment='文件MIME类型'),
    sa.Column('kzxx', PortableJSON(), nullable=True, comment='文件扩展信息JSON，包含时长、分辨率、校验值等元数据'),
    sa.Column('xxm', sa.String(length=64), nullable=False, comment='学校码'),
    sa.Column('cjr', sa.String(length=64), nullable=False, comment='创建人'),
    sa.Column('xgr', sa.String(length=64), nullable=False, comment='修改人'),
    sa.Column('cjsj', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='创建时间'),
    sa.Column('xgsj', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='修改时间'),
    sa.ForeignKeyConstraint(['rwzj'], ['media_task.zj'], name='fk_mt_wj_rw', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('zj', name=op.f('pk_media_artifact')),
    comment='媒体文件表',
    **MYSQL_TABLE_OPTIONS,
    )
    op.create_index('idx_mt_wj_lx', 'media_artifact', ['xxm', 'wjlx'], unique=False)
    op.create_index('idx_mt_wj_rw', 'media_artifact', ['rwzj'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_mt_wj_rw', table_name='media_artifact')
    op.drop_index('idx_mt_wj_lx', table_name='media_artifact')
    op.drop_table('media_artifact')
    op.drop_index('idx_ai_pkbz_zt', table_name='content_evaluation_step')
    op.drop_table('content_evaluation_step')
    op.drop_index('idx_ai_pkjl_zt', table_name='content_evaluation')
    op.drop_table('content_evaluation')
    op.drop_index('idx_mt_rw_zxjd', table_name='media_task')
    op.drop_index('idx_mt_rw_td', table_name='media_task')
    op.drop_index('idx_mt_rw_lzyy', table_name='media_task')
    op.drop_index('idx_mt_rw_lzll', table_name='media_task')
    op.drop_index('idx_mt_rw_fb', table_name='media_task')
    op.drop_index('idx_mt_rw_dd', table_name='media_task')
    op.drop_table('media_task')
    op.drop_index('idx_mt_fwq_zt', table_name='recording_server')
    op.drop_table('recording_server')
    op.drop_index('idx_mt_lb_jd', table_name='media_stream_binding')
    op.drop_table('media_stream_binding')
    op.drop_index('idx_ai_tsbb_qy', table_name='prompt_version')
    op.drop_table('prompt_version')
    op.drop_index('idx_mt_jd_zt_xt', table_name='media_node')
    op.drop_table('media_node')
