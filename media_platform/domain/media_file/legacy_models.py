from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime

class MediaFileBase(BaseModel):
    """媒体文件基础模型"""
    file_name: str = Field(..., description="文件名")
    file_url: str = Field(..., description="文件完整URL")
    relative_path: str = Field(..., description="文件相对路径")
    bucket_name: Optional[str] = Field(None, description="存储桶名称")
    file_size: int = Field(..., description="文件大小(字节)")
    mime_type: str = Field(..., description="文件MIME类型")
    task_id: str = Field(..., description="关联任务ID")
    file_metadata: Optional[Dict[str, Any]] = Field(None, description="文件扩展元数据")

class MediaFileCreate(MediaFileBase):
    """媒体文件创建模型"""
    id: str = Field(..., description="文件ID")

class MediaFileUpdate(BaseModel):
    """媒体文件更新模型"""
    file_name: Optional[str] = Field(None, description="文件名")
    file_url: Optional[str] = Field(None, description="文件完整URL")
    relative_path: Optional[str] = Field(None, description="文件相对路径")
    bucket_name: Optional[str] = Field(None, description="存储桶名称")
    file_size: Optional[int] = Field(None, description="文件大小(字节)")
    mime_type: Optional[str] = Field(None, description="文件MIME类型")
    file_metadata: Optional[Dict[str, Any]] = Field(None, description="文件扩展元数据")

class MediaFile(MediaFileBase):
    """媒体文件完整模型"""
    id: str = Field(..., description="文件ID")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: Optional[datetime] = Field(None, description="更新时间")

    class Config:
        from_attributes = True
