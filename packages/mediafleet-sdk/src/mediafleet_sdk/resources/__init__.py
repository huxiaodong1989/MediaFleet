"""MediaFleet SDK 资源入口。"""

from mediafleet_sdk.resources.admin import AdminResource, AsyncAdminResource
from mediafleet_sdk.resources.bindings import AsyncBindingsResource, BindingsResource
from mediafleet_sdk.resources.nodes import AsyncNodesResource, NodesResource
from mediafleet_sdk.resources.recording_servers import (
    AsyncRecordingServersResource,
    RecordingServersResource,
)
from mediafleet_sdk.resources.recordings import AsyncRecordingsResource, RecordingsResource
from mediafleet_sdk.resources.security import AsyncSecurityResource, SecurityResource
from mediafleet_sdk.resources.system import AsyncSystemResource, SystemResource
from mediafleet_sdk.resources.tasks import AsyncTasksResource, TasksResource

__all__ = [
    "AdminResource",
    "AsyncAdminResource",
    "AsyncBindingsResource",
    "AsyncNodesResource",
    "AsyncRecordingServersResource",
    "AsyncRecordingsResource",
    "AsyncSecurityResource",
    "AsyncSystemResource",
    "AsyncTasksResource",
    "BindingsResource",
    "NodesResource",
    "RecordingServersResource",
    "RecordingsResource",
    "SecurityResource",
    "SystemResource",
    "TasksResource",
]
