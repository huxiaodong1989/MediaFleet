"""RabbitMQ 拓扑常量和命名函数。

本模块只描述跨服务共享的稳定名称，不执行任何 RabbitMQ 网络操作。生产者、
消费者和部署脚本必须复用这里的名称，避免不同服务各自拼接后出现拓扑漂移。
"""

import re

MEDIA_TASK_EXCHANGE = "media.task"
MEDIA_TASK_RETRY_EXCHANGE = "media.task.retry"
MEDIA_TASK_DEAD_LETTER_EXCHANGE = "media.task.dlx"
CONTENT_ANALYSIS_TASK_EXCHANGE = "content.analysis.task"
CONTENT_ANALYSIS_TASK_RETRY_EXCHANGE = "content.analysis.task.retry"
CONTENT_ANALYSIS_TASK_DEAD_LETTER_EXCHANGE = "content.analysis.task.dlx"
CONTENT_ANALYSIS_TASK_QUEUE = "content-analysis.tasks"
MEDIA_COMMAND_EXCHANGE = "media.command"
MEDIA_COMMAND_RETRY_EXCHANGE = "media.command.retry"
MEDIA_COMMAND_DEAD_LETTER_EXCHANGE = "media.command.dlx"
MEDIA_EVENT_EXCHANGE = "media.event"
MEDIA_EVENT_RETRY_EXCHANGE = "media.event.retry"
MEDIA_EVENT_DEAD_LETTER_EXCHANGE = "media.event.dlx"

CONTROL_CENTER_EVENT_QUEUE = "control-center.media-events"

_RABBITMQ_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def normalized_rabbitmq_name(value: str, *, field_name: str) -> str:
    """校验并返回可安全用于 Exchange、Queue 或 Routing Key 的名称。

    RabbitMQ 本身允许更宽泛的字符，但本项目主动限制为字母、数字、点、下划线
    和短横线，使环境变量、监控标签和运维命令中的名称保持可预测。
    """

    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} 不能为空")
    if not _RABBITMQ_NAME_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"{field_name} 只能包含字母、数字、点、下划线和短横线"
        )
    return normalized


def normalized_binding_key(value: str) -> str:
    """校验并返回 RabbitMQ Topic Exchange 的绑定键。

    绑定键允许 RabbitMQ 原生的 ``*`` 和 ``#`` 通配符。当前通用媒体 Worker
    默认使用 ``#`` 绑定共享任务队列，从而让所有 Worker 实例竞争处理全部任务。
    """

    normalized = value.strip()
    if not normalized:
        raise ValueError("routing_key 不能为空")
    if any(character.isspace() for character in normalized):
        raise ValueError("routing_key 不能包含空白字符")
    return normalized


def _normalized_node_id(node_id: str) -> str:
    return normalized_rabbitmq_name(node_id, field_name="node_id")


def worker_retry_queue_name(queue_name: str) -> str:
    """返回某个能力主队列对应的延迟重试队列名称。"""

    return f"{normalized_rabbitmq_name(queue_name, field_name='queue_name')}.retry"


def worker_dead_letter_queue_name(queue_name: str) -> str:
    """返回某个能力主队列对应的最终死信队列名称。"""

    return f"{normalized_rabbitmq_name(queue_name, field_name='queue_name')}.dlq"


def queue_retry_queue_name(queue_name: str) -> str:
    """返回通用队列对应的延迟重试队列名称。"""

    return f"{normalized_rabbitmq_name(queue_name, field_name='queue_name')}.retry"


def queue_dead_letter_queue_name(queue_name: str) -> str:
    """返回通用队列对应的最终死信队列名称。"""

    return f"{normalized_rabbitmq_name(queue_name, field_name='queue_name')}.dlq"


def recorder_command_routing_key(node_id: str) -> str:
    """返回录制节点定向命令 Routing Key。"""

    return f"recorder.{_normalized_node_id(node_id)}"


def recorder_command_queue_name(node_id: str) -> str:
    """返回录制节点持久化命令队列名。"""

    return f"{recorder_command_routing_key(node_id)}.commands"
