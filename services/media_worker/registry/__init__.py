"""媒体处理器注册表。"""

from services.media_worker.registry.processor_registry import (
    DuplicateProcessorError,
    MediaTaskProcessor,
    ProcessorResult,
    TaskProcessorRegistry,
    UnsupportedTaskTypeError,
)

__all__ = [
    "DuplicateProcessorError",
    "MediaTaskProcessor",
    "ProcessorResult",
    "TaskProcessorRegistry",
    "UnsupportedTaskTypeError",
]
