import asyncio
from threading import Event

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from media_platform.contracts.task import TaskDeliveryChannel, TaskDispatchMessage
from media_platform.infrastructure.database.models import (
    ContentEvaluationRecordModel,
    ContentEvaluationStepModel,
    ContentPromptBundleModel,
    MediaTaskModel,
)
from services.content_analysis.application.prompt_defaults import (
    load_bootstrap_prompt_bundle,
)
from services.content_analysis.application.workflow import ClassEvaluationWorkflow
from services.content_analysis.infrastructure.clients import BehaviorAnalysisClient
from services.content_analysis.infrastructure.repositories import PromptBundleRepository


class SubtitleClient:
    async def download(self, _url):
        return "教师讲解课堂内容。"


class FilePreprocessor:
    async def process(self, *_args, **_kwargs):
        raise AssertionError("没有附件时不应调用文件预处理")


class LlmClient:
    def __init__(self):
        self.calls = 0

    async def analyze(self, **_kwargs):
        self.calls += 1
        return {f"stepResult{self.calls}": self.calls}, {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "total_tokens": 12,
        }


class CallbackClient:
    async def notify(self, *_args, **_kwargs):
        return True


def test_workflow_marks_missing_behavior_analysis_as_degraded(tmp_path):
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'workflow.db').as_posix()}")
    MediaTaskModel.__table__.create(engine)
    ContentPromptBundleModel.__table__.create(engine)
    ContentEvaluationRecordModel.__table__.create(engine)
    ContentEvaluationStepModel.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with factory.begin() as session:
            session.add(
                MediaTaskModel(
                    id="task-1",
                    business_task_id="business-1",
                    task_type="content.class_evaluation",
                    delivery_channel=TaskDeliveryChannel.CONTENT_ANALYSIS.value,
                    routing_key="content.class_evaluation",
                    status="processing",
                    params={},
                    publish_status="PUBLISHED",
                    message_id="message-1",
                    execution_generation=1,
                    school_code="school-1",
                    created_by="test",
                    updated_by="test",
                )
            )
            PromptBundleRepository(session).ensure_bootstrap(
                load_bootstrap_prompt_bundle("test-model")
            )

        llm_client = LlmClient()
        workflow = ClassEvaluationWorkflow(
            factory,
            subtitle_client=SubtitleClient(),
            behavior_client=BehaviorAnalysisClient(None),
            file_preprocessor=FilePreprocessor(),
            llm_client=llm_client,
            progress_callback=CallbackClient(),
            llm_max_retries=1,
        )
        message = TaskDispatchMessage(
            message_id="message-1",
            task_id="task-1",
            business_task_id="business-1",
            school_code="school-1",
            task_type="content.class_evaluation",
            routing_key="content.class_evaluation",
            delivery_channel=TaskDeliveryChannel.CONTENT_ANALYSIS,
            params={
                "taskId": "business-1",
                "schoolCode": "school-1",
                "classroomId": "classroom-1",
                "videoMetadata": {
                    "durationSeconds": 60,
                    "fileSizeMb": 1,
                    "resolution": "1920x1080",
                    "format": "mp4",
                    "createdAt": "2026-09-28T10:00:00",
                },
                "subtitleUrl": "https://example.invalid/subtitle.txt",
                "evaluationForm": {
                    "aiFormId": "form-1",
                    "formType": 1,
                    "courseName": "语文",
                },
                "enableFilePreprocessing": False,
            },
        )

        outcome = asyncio.run(workflow.execute(message, 1, Event()))

        assert llm_client.calls == 8
        assert outcome.result["status"] == "COMPLETED"
        assert outcome.result["degraded"] is True
        assert outcome.result["missingInputs"] == ["behaviorAnalysis"]
        assert outcome.result["failedSteps"] == []
    finally:
        engine.dispose()
