from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

from media_platform.domain.task.legacy_models import TaskStatus, TaskType



class ExtractAudioResult(BaseModel):
    """拆分音频结果"""
    task_id:str
    status: Optional[TaskStatus] = None
    progress: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    worker_node: Optional[str] = None
    # started_at: Optional[datetime] = None
    # completed_at: Optional[datetime] = None
    # retry_count: Optional[int] = None
    video_url:Optional[str]=None
    audio_url:Optional[str]=None
