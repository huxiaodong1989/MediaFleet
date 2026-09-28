"""AI 评课业务记录、步骤检查点和提示词版本仓储。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from media_platform.infrastructure.database.repositories.content_prompt_bundle import (
    GLOBAL_SCHOOL_CODE,
    PROMPT_SCHEME_CODE,
    PromptBundleRepository,
    prompt_content_hash,
)
from services.content_analysis.infrastructure.models import (
    ContentEvaluationRecordModel,
    ContentEvaluationStepModel,
)

class ContentEvaluationRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_record(self, task_id: str) -> ContentEvaluationRecordModel | None:
        return self.session.scalar(
            select(ContentEvaluationRecordModel).where(ContentEvaluationRecordModel.task_id == task_id)
        )

    def get_by_business_task_id(self, business_task_id: str) -> ContentEvaluationRecordModel | None:
        return self.session.scalar(
            select(ContentEvaluationRecordModel)
            .where(ContentEvaluationRecordModel.business_task_id == business_task_id)
            .order_by(ContentEvaluationRecordModel.created_at.desc())
            .limit(1)
        )

    def start_execution(
        self,
        *,
        task_id: str,
        business_task_id: str,
        classroom_id: str,
        school_code: str,
        execution_generation: int,
        prompt_bundle_id: str,
        material_digest: str,
        model_version: str,
        request_snapshot: dict[str, Any],
        operator: str,
    ) -> ContentEvaluationRecordModel:
        record = self.get_record(task_id)
        if record is None:
            record = ContentEvaluationRecordModel(
                id=str(uuid4()),
                task_id=task_id,
                business_task_id=business_task_id,
                classroom_id=classroom_id,
                status="processing",
                progress=0,
                execution_generation=execution_generation,
                prompt_bundle_id=prompt_bundle_id,
                material_digest=material_digest,
                model_version=model_version,
                request_snapshot=request_snapshot,
                school_code=school_code,
                created_by=operator,
                updated_by=operator,
            )
            self.session.add(record)
        elif execution_generation >= record.execution_generation:
            record.status = "processing"
            record.progress = 0
            record.current_step = None
            record.execution_generation = execution_generation
            record.prompt_bundle_id = prompt_bundle_id
            record.material_digest = material_digest
            record.model_version = model_version
            record.request_snapshot = request_snapshot
            record.error_message = None
            record.updated_by = operator
        self.session.flush()
        return record

    def find_reusable_step(
        self,
        *,
        task_id: str,
        step_code: str,
        input_digest: str,
        prompt_bundle_id: str,
        model_version: str,
    ) -> ContentEvaluationStepModel | None:
        return self.session.scalar(
            select(ContentEvaluationStepModel)
            .where(
                ContentEvaluationStepModel.task_id == task_id,
                ContentEvaluationStepModel.step_code == step_code,
                ContentEvaluationStepModel.status == "completed",
                ContentEvaluationStepModel.input_digest == input_digest,
                ContentEvaluationStepModel.prompt_bundle_id == prompt_bundle_id,
                ContentEvaluationStepModel.model_version == model_version,
            )
            .order_by(ContentEvaluationStepModel.execution_generation.desc())
            .limit(1)
        )

    def begin_step(self, *, task_id: str, execution_generation: int, step_code: str, sort_order: int, input_digest: str, prompt_bundle_id: str, model_version: str, school_code: str, operator: str) -> ContentEvaluationStepModel:
        step = self.session.scalar(
            select(ContentEvaluationStepModel).where(
                ContentEvaluationStepModel.task_id == task_id,
                ContentEvaluationStepModel.execution_generation == execution_generation,
                ContentEvaluationStepModel.step_code == step_code,
            )
        )
        if step is None:
            step = ContentEvaluationStepModel(
                id=str(uuid4()), task_id=task_id, execution_generation=execution_generation,
                step_code=step_code, sort_order=sort_order, status="running",
                input_digest=input_digest, prompt_bundle_id=prompt_bundle_id,
                model_version=model_version, started_at=datetime.now(),
                school_code=school_code, created_by=operator, updated_by=operator,
            )
            self.session.add(step)
        else:
            step.status = "running"
            step.started_at = datetime.now()
            step.completed_at = None
            step.error_message = None
            step.updated_by = operator
        record = self.get_record(task_id)
        if record is None or record.execution_generation != execution_generation:
            raise RuntimeError("评课记录执行代次已经变化")
        record.current_step = step_code
        record.updated_by = operator
        self.session.flush()
        return step

    def reuse_step(
        self,
        *,
        task_id: str,
        execution_generation: int,
        step_code: str,
        sort_order: int,
        input_digest: str,
        prompt_bundle_id: str,
        model_version: str,
        result: dict[str, Any],
        token_usage: dict[str, Any] | None,
        progress: float,
        aggregate_result: dict[str, Any],
        school_code: str,
        operator: str,
    ) -> bool:
        step = self.begin_step(
            task_id=task_id,
            execution_generation=execution_generation,
            step_code=step_code,
            sort_order=sort_order,
            input_digest=input_digest,
            prompt_bundle_id=prompt_bundle_id,
            model_version=model_version,
            school_code=school_code,
            operator=operator,
        )
        record = self.get_record(task_id)
        if record is None or record.execution_generation != execution_generation:
            return False
        step.status = "skipped"
        step.result = result
        step.token_usage = token_usage
        step.completed_at = datetime.now()
        step.updated_by = operator
        record.progress = progress
        record.result = aggregate_result
        record.updated_by = operator
        self.session.flush()
        return True

    def complete_step(self, *, task_id: str, execution_generation: int, step_code: str, result: dict[str, Any], token_usage: dict[str, Any], progress: float, aggregate_result: dict[str, Any], operator: str) -> bool:
        step = self.session.scalar(
            select(ContentEvaluationStepModel).where(
                ContentEvaluationStepModel.task_id == task_id,
                ContentEvaluationStepModel.execution_generation == execution_generation,
                ContentEvaluationStepModel.step_code == step_code,
            )
        )
        record = self.get_record(task_id)
        if step is None or record is None or record.execution_generation != execution_generation:
            return False
        step.status = "completed"
        step.result = result
        step.token_usage = token_usage
        step.error_message = None
        step.completed_at = datetime.now()
        step.updated_by = operator
        record.progress = progress
        record.result = aggregate_result
        record.error_message = None
        record.updated_by = operator
        self.session.flush()
        return True

    def fail_step(self, *, task_id: str, execution_generation: int, step_code: str, error_message: str, aggregate_result: dict[str, Any], progress: float, operator: str) -> bool:
        step = self.session.scalar(
            select(ContentEvaluationStepModel).where(
                ContentEvaluationStepModel.task_id == task_id,
                ContentEvaluationStepModel.execution_generation == execution_generation,
                ContentEvaluationStepModel.step_code == step_code,
            )
        )
        record = self.get_record(task_id)
        if step is None or record is None or record.execution_generation != execution_generation:
            return False
        step.status = "failed"
        step.error_message = error_message
        step.completed_at = datetime.now()
        step.updated_by = operator
        record.progress = progress
        record.result = aggregate_result
        record.error_message = error_message
        record.updated_by = operator
        self.session.flush()
        return True

    def finish(self, *, task_id: str, execution_generation: int, status: str, result: dict[str, Any], error_message: str | None, operator: str) -> bool:
        record = self.get_record(task_id)
        if record is None or record.execution_generation != execution_generation:
            return False
        record.status = status
        record.progress = 100
        record.current_step = None
        record.result = result
        record.error_message = error_message
        record.updated_by = operator
        self.session.flush()
        return True


__all__ = [
    "ContentEvaluationRepository",
    "GLOBAL_SCHOOL_CODE",
    "PROMPT_SCHEME_CODE",
    "PromptBundleRepository",
    "prompt_content_hash",
]
