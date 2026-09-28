"""录制命令目标节点解析工具。"""

from __future__ import annotations

from media_platform.domain.stream import MediaStreamBindingResult


def recorder_command_target(binding: MediaStreamBindingResult) -> str:
    """返回 recorder-node 命令队列使用的目标节点编号。

    `MediaStreamBindingResult.node_id` 是 `media_node` 的数据库主键，用于绑定表
    外键和查询；RabbitMQ 命令队列绑定的是 recorder-node 启动时上报的
    `node_code`，也就是 `RECORDER_NODE_ID`。如果用数据库主键发布命令，Broker
    找不到 `recorder.<node_id>.commands` 队列，会返回 unroutable。

    历史测试或脏数据可能没有携带节点快照，此时兜底使用 `node_id`，但正常生产
    绑定必须能解析到 `node.node_code`。
    """

    node_code = (binding.node.node_code if binding.node is not None else None) or ""
    normalized = str(node_code).strip()
    if normalized:
        return normalized
    return str(binding.node_id).strip()


__all__ = ["recorder_command_target"]
