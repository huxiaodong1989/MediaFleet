"""录制节点录像合并、提取、上传和清理编排。"""

from services.recorder_node.postprocess.cleanup_service import RecordingCleanupService
from services.recorder_node.postprocess.media_processing_service import (
    RecordingMediaProcessingService,
)
from services.recorder_node.postprocess.persistence_service import (
    RecordingPostProcessPersistenceService,
)
from services.recorder_node.postprocess.preparation_service import (
    RecordingPreparationService,
)
from services.recorder_node.postprocess.post_processor import (
    PostProcessTask,
    PostProcessingManager,
    get_post_processing_manager,
)
from services.recorder_node.postprocess.record_file_cleaner import RecordFileCleaner
from services.recorder_node.postprocess.stage_executor import (
    RecordingPostProcessStageExecutor,
)
from services.recorder_node.postprocess.stages import (
    CRITICAL_FAILURE_STAGES,
    DEFAULT_POST_PROCESS_STAGES,
    PostProcessStage,
    format_stage_list,
    stage_label,
)
from services.recorder_node.postprocess.upload_service import RecordingUploadService

__all__ = [
    "CRITICAL_FAILURE_STAGES",
    "DEFAULT_POST_PROCESS_STAGES",
    "PostProcessStage",
    "PostProcessTask",
    "PostProcessingManager",
    "RecordFileCleaner",
    "RecordingCleanupService",
    "RecordingMediaProcessingService",
    "RecordingPostProcessPersistenceService",
    "RecordingPreparationService",
    "RecordingPostProcessStageExecutor",
    "RecordingUploadService",
    "format_stage_list",
    "get_post_processing_manager",
    "stage_label",
]
