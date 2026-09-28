"""调用中心 HTTP API。"""

from services.control_center.api.admin import router as admin_router
from services.control_center.api.node_heartbeat import router as node_heartbeat_router
from services.control_center.api.node_selection import router as node_selection_router
from services.control_center.api.recording_commands import (
    router as recording_command_router,
)
from services.control_center.api.recording_servers import (
    router as recording_server_router,
)
from services.control_center.api.stream_bindings import (
    router as stream_binding_router,
)
from services.control_center.api.tasks import router as task_router

__all__ = [
    "admin_router",
    "node_heartbeat_router",
    "node_selection_router",
    "recording_command_router",
    "recording_server_router",
    "stream_binding_router",
    "task_router",
]
