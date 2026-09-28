"""AI 评课 HTTP、消息参数与提示词方案契约。"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    model_validator,
)


class ContentModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class Metric(ContentModel):
    description: list[dict[str, Any]] | None = None
    metric_code: str | None = Field(None, validation_alias=AliasChoices("metricCode", "metric_code"), serialization_alias="metricCode")
    metric_name: str | None = Field(None, validation_alias=AliasChoices("metricName", "metric_name"), serialization_alias="metricName")
    status: int | None = None


class FormContent(ContentModel):
    dimension_code: str | None = Field(None, validation_alias=AliasChoices("dimensionCode", "dimension_code"), serialization_alias="dimensionCode")
    dimension_name: str | None = Field(None, validation_alias=AliasChoices("dimensionName", "dimension_name"), serialization_alias="dimensionName")
    metric_info: list[Metric] | None = Field(None, validation_alias=AliasChoices("metricInfo", "metric_info"), serialization_alias="metricInfo")
    value: int | None = None


class WeightSetting(ContentModel):
    dimension_code: str | None = Field(None, validation_alias=AliasChoices("dimensionCode", "dimension_code"), serialization_alias="dimensionCode")
    dimension_name: str | None = Field(None, validation_alias=AliasChoices("dimensionName", "dimension_name"), serialization_alias="dimensionName")
    value: int | None = None


class AiForm(ContentModel):
    ai_form_id: str = Field(validation_alias=AliasChoices("aiFormId", "ai_form_id"), serialization_alias="aiFormId")
    course_or_dept_id: str | None = Field(None, validation_alias=AliasChoices("courseOrDeptId", "course_or_dept_id"), serialization_alias="courseOrDeptId")
    form_content: list[FormContent] | None = Field(None, validation_alias=AliasChoices("formContent", "form_content"), serialization_alias="formContent")
    form_name: str | None = Field(None, validation_alias=AliasChoices("formName", "form_name"), serialization_alias="formName")
    form_type: int = Field(validation_alias=AliasChoices("formType", "form_type"), serialization_alias="formType")
    weight_setting: list[WeightSetting] | None = Field(None, validation_alias=AliasChoices("weightSetting", "weight_setting"), serialization_alias="weightSetting")
    course_name: str | None = Field(None, validation_alias=AliasChoices("courseName", "course_name"), serialization_alias="courseName")


class FileType(str, Enum):
    PDF = "pdf"
    DOCX = "docx"
    PPT = "ppt"
    HTML = "html"
    IMAGE = "image"
    CSV = "csv"
    JSON = "json"
    TXT = "txt"
    UNKNOWN = "unknown"


class FileMetadata(ContentModel):
    file_name: str = Field(validation_alias=AliasChoices("fileName", "file_name"), serialization_alias="fileName")
    file_url: HttpUrl = Field(validation_alias=AliasChoices("fileUrl", "file_url"), serialization_alias="fileUrl")
    file_type: FileType = Field(validation_alias=AliasChoices("fileType", "file_type"), serialization_alias="fileType")
    file_size_mb: float | None = Field(None, validation_alias=AliasChoices("fileSizeMb", "file_size_mb"), serialization_alias="fileSizeMb")
    description: str | None = None
    upload_time: datetime | None = Field(None, validation_alias=AliasChoices("uploadTime", "upload_time"), serialization_alias="uploadTime")


class FileCollection(ContentModel):
    files: list[FileMetadata] = Field(default_factory=list)
    category: str = ""


class VideoMetadata(ContentModel):
    duration_seconds: int = Field(validation_alias=AliasChoices("durationSeconds", "duration_seconds"), serialization_alias="durationSeconds")
    file_size_mb: float = Field(validation_alias=AliasChoices("fileSizeMb", "file_size_mb"), serialization_alias="fileSizeMb")
    resolution: str
    format: str
    created_at: datetime = Field(validation_alias=AliasChoices("createdAt", "created_at"), serialization_alias="createdAt")


class ClassEvaluationRequest(ContentModel):
    task_id: str = Field(min_length=1, max_length=128, validation_alias=AliasChoices("taskId", "task_id"), serialization_alias="taskId")
    school_code: str = Field(
        "GLOBAL",
        min_length=1,
        max_length=64,
        validation_alias=AliasChoices("schoolCode", "school_code"),
        serialization_alias="schoolCode",
    )
    tenant_id: int | None = Field(None, validation_alias=AliasChoices("tenantId", "tenant_id"), serialization_alias="tenantId")
    classroom_id: str = Field(min_length=1, max_length=128, validation_alias=AliasChoices("classroomId", "classroom_id"), serialization_alias="classroomId")
    video_metadata: VideoMetadata = Field(validation_alias=AliasChoices("videoMetadata", "video_metadata"), serialization_alias="videoMetadata")
    subtitle_url: HttpUrl = Field(validation_alias=AliasChoices("subtitleUrl", "subtitle_url"), serialization_alias="subtitleUrl")
    evaluation_form: AiForm = Field(validation_alias=AliasChoices("evaluationForm", "evaluation_form"), serialization_alias="evaluationForm")
    courseware_files: FileCollection | None = Field(None, validation_alias=AliasChoices("coursewareFiles", "courseware_files"), serialization_alias="coursewareFiles")
    syllabus_files: FileCollection | None = Field(None, validation_alias=AliasChoices("syllabusFiles", "syllabus_files"), serialization_alias="syllabusFiles")
    knowledge_graph_files: FileCollection | None = Field(None, validation_alias=AliasChoices("knowledgeGraphFiles", "knowledge_graph_files"), serialization_alias="knowledgeGraphFiles")
    enable_file_preprocessing: bool = Field(True, validation_alias=AliasChoices("enableFilePreprocessing", "enable_file_preprocessing"), serialization_alias="enableFilePreprocessing")
    max_text_chunk_size: int = Field(4000, ge=0, le=50000, validation_alias=AliasChoices("maxTextChunkSize", "max_text_chunk_size"), serialization_alias="maxTextChunkSize")
    extract_images: bool = Field(True, validation_alias=AliasChoices("extractImages", "extract_images"), serialization_alias="extractImages")
    callback_url: HttpUrl | None = Field(None, validation_alias=AliasChoices("callbackUrl", "callback_url"), serialization_alias="callbackUrl")
    webhook_token: str | None = Field(None, validation_alias=AliasChoices("webhookToken", "webhook_token"), serialization_alias="webhookToken")
    metadata: dict[str, Any] | None = None
    quest_type: int | None = Field(None, validation_alias=AliasChoices("questType", "quest_type"), serialization_alias="questType")

    @model_validator(mode="before")
    @classmethod
    def fill_school_code_from_tenant(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if data.get("schoolCode") or data.get("school_code"):
            return data
        tenant_id = data.get("tenantId", data.get("tenant_id"))
        if tenant_id is not None and str(tenant_id).strip():
            data = dict(data)
            data["schoolCode"] = str(tenant_id).strip()
        return data


class ClassEvaluationCreateResponse(BaseModel):
    taskId: str
    status: str
    message: str
    internalTaskId: str
    created: bool


class ClassEvaluationRecordResponse(BaseModel):
    taskId: str
    internalTaskId: str
    classroomId: str
    status: str
    progress: float
    currentStep: str | None = None
    evaluationResult: dict[str, Any] | None = None
    errorMessage: str | None = None
    promptVersion: int | None = None
    createdAt: datetime | None = None
    updatedAt: datetime | None = None


class PromptStepDefinition(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    order: int = Field(ge=0)
    system_prompt: str = Field(min_length=1)
    user_prompt: str = Field(min_length=1)
    model: str = Field(min_length=1, max_length=128)
    temperature: float = Field(default=0.5, ge=0, le=2)
    max_tokens: int = Field(default=32768, ge=1)
    required_metadata: list[str] = Field(default_factory=list)
    critical: bool = False


class PromptBundleContent(BaseModel):
    steps: list[PromptStepDefinition] = Field(min_length=1)
    subtitle_edit_prompt: str | None = None

    @model_validator(mode="after")
    def validate_steps(self) -> "PromptBundleContent":
        expected_codes = {
            "CLASSROOM_SUMMARY",
            "FREQUENCY_ANALYSIS",
            "KEYEVENT_ANALYSIS",
            "CONTENT_SUMMARY",
            "QA_ANALYSIS",
            "TEACHING_METHOD",
            "KNOWLEDGE_GRAPH",
            "CLASSROOM_SCORE",
        }
        codes = [step.code for step in self.steps]
        orders = [step.order for step in self.steps]
        if len(codes) != len(set(codes)):
            raise ValueError("提示词步骤代码不能重复")
        if len(orders) != len(set(orders)):
            raise ValueError("提示词步骤顺序不能重复")
        if set(codes) != expected_codes:
            missing = sorted(expected_codes - set(codes))
            extra = sorted(set(codes) - expected_codes)
            raise ValueError(f"提示词方案步骤不完整: missing={missing}, extra={extra}")
        if codes.count("CLASSROOM_SCORE") != 1:
            raise ValueError("提示词方案必须且只能包含一个 CLASSROOM_SCORE 步骤")
        score_step = next(step for step in self.steps if step.code == "CLASSROOM_SCORE")
        if not score_step.critical:
            raise ValueError("CLASSROOM_SCORE 必须标记为关键步骤")
        return self


__all__ = [
    "ClassEvaluationCreateResponse",
    "ClassEvaluationRecordResponse",
    "ClassEvaluationRequest",
    "FileCollection",
    "FileMetadata",
    "FileType",
    "PromptBundleContent",
    "PromptStepDefinition",
]
