"""可恢复的八步 AI 评课工作流。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from hashlib import sha256
import json
import logging
from threading import Event
from time import perf_counter
from typing import Any, Callable

from sqlalchemy.orm import Session

from media_platform.contracts.content_evaluation import (
    ClassEvaluationRequest,
    PromptBundleContent,
    PromptStepDefinition,
)
from media_platform.contracts.task import TaskDispatchMessage
from services.content_analysis.infrastructure.clients import (
    BehaviorAnalysisClient,
    BehaviorAnalysisResult,
    EvaluationLlmClient,
    ProgressCallbackClient,
    SubtitleClient,
    safe_url,
)
from services.content_analysis.application.log_sanitizer import sanitize_log_value
from services.content_analysis.infrastructure.file_preprocessor import (
    EvaluationFilePreprocessor,
)
from services.content_analysis.infrastructure.repositories import (
    ContentEvaluationRepository,
    PromptBundleRepository,
)


LOGGER = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]


@dataclass(frozen=True)
class EvaluationOutcome:
    result: dict[str, Any]
    business_task_id: str
    callback_url: str | None
    callback_token: str | None


class LeaseLostError(RuntimeError):
    """当前实例已经失去公共任务执行租约。"""


class ClassEvaluationWorkflow:
    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        subtitle_client: SubtitleClient,
        behavior_client: BehaviorAnalysisClient,
        file_preprocessor: EvaluationFilePreprocessor,
        llm_client: EvaluationLlmClient,
        progress_callback: ProgressCallbackClient,
        llm_max_retries: int = 3,
    ) -> None:
        self.session_factory = session_factory
        self.subtitle_client = subtitle_client
        self.behavior_client = behavior_client
        self.file_preprocessor = file_preprocessor
        self.llm_client = llm_client
        self.progress_callback = progress_callback
        self.llm_max_retries = llm_max_retries

    @staticmethod
    def _digest(value: Any) -> str:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
        return sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _check_lease(lease_lost: Event) -> None:
        if lease_lost.is_set():
            raise LeaseLostError("评课执行租约已经丢失，停止旧执行者写回")

    @staticmethod
    def _request_snapshot(request: ClassEvaluationRequest) -> dict[str, Any]:
        snapshot = request.model_dump(mode="json", by_alias=True, exclude_none=True)
        snapshot.pop("webhookToken", None)
        if "subtitleUrl" in snapshot:
            snapshot["subtitleUrl"] = safe_url(str(snapshot["subtitleUrl"]))
        for key in ("coursewareFiles", "syllabusFiles", "knowledgeGraphFiles"):
            collection = snapshot.get(key)
            if not isinstance(collection, dict):
                continue
            for file in collection.get("files", []):
                if isinstance(file, dict) and file.get("fileUrl"):
                    file["fileUrl"] = safe_url(str(file["fileUrl"]))
        return snapshot

    async def _prepare_files(self, request: ClassEvaluationRequest) -> list[dict[str, Any]]:
        if not request.enable_file_preprocessing:
            return []
        jobs = []
        for category, collection in (
            ("courseware", request.courseware_files),
            ("syllabus", request.syllabus_files),
            ("knowledge_graph", request.knowledge_graph_files),
        ):
            if collection is None:
                continue
            for file in collection.files:
                jobs.append(self.file_preprocessor.process(file, category=category, extract_images=request.extract_images))
        if not jobs:
            return []
        # 调用方明确传入的材料是评课输入；任一材料失败都不能静默跳过。
        return list(await asyncio.gather(*jobs))

    @staticmethod
    def _analysis_content(
        request: ClassEvaluationRequest,
        *,
        subtitle: str,
        behavior: dict[str, Any] | None,
        files: list[dict[str, Any]],
    ) -> str:
        parts = [
            "=== 视频元数据 ===",
            json.dumps(request.video_metadata.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2),
            "",
            "=== 课堂行为分析数据 ===",
            json.dumps(behavior or {"status": "NO_DATA"}, ensure_ascii=False, indent=2),
            "",
            "=== 评课表单 ===",
            json.dumps(request.evaluation_form.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2),
            "",
            "=== 课件、大纲和知识图谱材料 ===",
        ]
        if files:
            for item in files:
                text = item["text"]
                if len(text) > 15000:
                    text = text[:15000] + "\n[材料内容已截断]"
                parts.extend([f"--- {item['category']} / {item['file_name']} ---", text, ""])
        else:
            parts.extend(["无附加材料。", ""])
        parts.append("=== 课堂语音转写 ===")
        parts.append(subtitle[:25000] + ("\n[字幕内容已截断]" if len(subtitle) > 25000 else ""))
        return "\n".join(parts)

    @staticmethod
    def _resolve_prompt(step: PromptStepDefinition, metadata: dict[str, Any] | None) -> str:
        prompt = step.system_prompt
        metadata = metadata or {}
        for name in step.required_metadata:
            if name not in metadata:
                raise ValueError(f"执行步骤缺少必要元数据: {name}")
            prompt = prompt.replace(f"{{{{{name}}}}}", str(metadata[name]))
        return prompt

    async def _run_step(
        self,
        step: PromptStepDefinition,
        analysis_content: str,
        resolved_prompt: str,
        *,
        task_id: str,
        business_task_id: str,
        step_position: int,
        total_steps: int,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        last_error: Exception | None = None
        for attempt in range(self.llm_max_retries):
            try:
                return await self.llm_client.analyze(
                    model=step.model,
                    system_prompt=f"{analysis_content}\n\n{resolved_prompt}",
                    user_prompt=step.user_prompt,
                    temperature=step.temperature,
                    max_tokens=step.max_tokens,
                )
            except Exception as exc:
                last_error = exc
                will_retry = attempt + 1 < self.llm_max_retries
                LOGGER.warning(
                    "AI评课步骤模型调用失败: task_id=%s, business_task_id=%s, "
                    "step=%s/%s, step_code=%s, model=%s, model_attempt=%s/%s, "
                    "will_retry=%s, error_type=%s, error=%s",
                    task_id,
                    sanitize_log_value(business_task_id),
                    step_position,
                    total_steps,
                    step.code,
                    sanitize_log_value(step.model),
                    attempt + 1,
                    self.llm_max_retries,
                    will_retry,
                    type(exc).__name__,
                    sanitize_log_value(exc, max_length=500),
                )
                if will_retry:
                    await asyncio.sleep(2**attempt)
        raise RuntimeError(f"步骤 {step.code} 模型调用失败") from last_error

    async def execute(self, message: TaskDispatchMessage, execution_generation: int, lease_lost: Event) -> EvaluationOutcome:
        workflow_started_at = perf_counter()
        request = ClassEvaluationRequest.model_validate(message.params)
        business_task_id = message.business_task_id or request.task_id
        LOGGER.info(
            "AI评课工作流初始化: task_id=%s, business_task_id=%s, classroom_id=%s, "
            "course_name=%s, ai_form_id=%s, school_code=%s, generation=%s",
            message.task_id,
            sanitize_log_value(business_task_id),
            sanitize_log_value(request.classroom_id),
            sanitize_log_value(request.evaluation_form.course_name),
            sanitize_log_value(request.evaluation_form.ai_form_id),
            sanitize_log_value(message.school_code),
            execution_generation,
        )
        self._check_lease(lease_lost)

        with self.session_factory() as session:
            prompt_row = PromptBundleRepository(session).get_published(message.school_code)
            if prompt_row is None:
                raise RuntimeError("没有已发布的评课提示词版本")
            prompt_bundle = PromptBundleContent.model_validate(prompt_row.content)
            prompt_bundle_id = prompt_row.id
            prompt_version = prompt_row.version

        LOGGER.info(
            "AI评课提示词版本已锁定: task_id=%s, business_task_id=%s, "
            "prompt_version=%s, prompt_bundle_id=%s, step_count=%s",
            message.task_id,
            sanitize_log_value(business_task_id),
            prompt_version,
            prompt_bundle_id,
            len(prompt_bundle.steps),
        )

        material_started_at = perf_counter()
        LOGGER.info(
            "AI评课材料准备开始: task_id=%s, business_task_id=%s, "
            "classroom_id=%s, file_preprocessing=%s",
            message.task_id,
            sanitize_log_value(business_task_id),
            sanitize_log_value(request.classroom_id),
            request.enable_file_preprocessing,
        )
        subtitle_task = self.subtitle_client.download(str(request.subtitle_url))
        behavior_task = self.behavior_client.get(classroom_id=request.classroom_id, tenant_id=request.tenant_id)
        subtitle, behavior_result = await asyncio.gather(
            subtitle_task,
            behavior_task,
        )
        if not isinstance(behavior_result, BehaviorAnalysisResult):
            raise RuntimeError("行为分析客户端返回了无效结果类型")
        behavior = behavior_result.data
        missing_inputs = [] if behavior_result.available else ["behaviorAnalysis"]
        if not behavior_result.available:
            LOGGER.warning(
                "AI评课行为数据不可用，将以降级模式继续: task_id=%s, "
                "business_task_id=%s, classroom_id=%s, reason=%s",
                message.task_id,
                sanitize_log_value(business_task_id),
                sanitize_log_value(request.classroom_id),
                sanitize_log_value(behavior_result.missing_reason),
            )
        files = await self._prepare_files(request)
        analysis_content = self._analysis_content(request, subtitle=subtitle, behavior=behavior, files=files)
        material_digest = self._digest({"subtitle": subtitle, "behavior": behavior, "files": files, "form": request.evaluation_form.model_dump(mode="json")})
        model_version = ",".join(sorted({step.model for step in prompt_bundle.steps}))
        operator = message.source or "content-analysis"
        LOGGER.info(
            "AI评课材料准备完成: task_id=%s, business_task_id=%s, "
            "subtitle_chars=%s, behavior_available=%s, attachment_count=%s, "
            "model_version=%s, duration_ms=%s",
            message.task_id,
            sanitize_log_value(business_task_id),
            len(subtitle),
            behavior is not None,
            len(files),
            sanitize_log_value(model_version),
            round((perf_counter() - material_started_at) * 1000),
        )

        with self.session_factory() as session:
            with session.begin():
                ContentEvaluationRepository(session).start_execution(
                    task_id=message.task_id,
                    business_task_id=business_task_id,
                    classroom_id=request.classroom_id,
                    school_code=message.school_code,
                    execution_generation=execution_generation,
                    prompt_bundle_id=prompt_bundle_id,
                    material_digest=material_digest,
                    model_version=model_version,
                    request_snapshot=self._request_snapshot(request),
                    operator=operator,
                )

        aggregate: dict[str, Any] = {}
        token_steps: list[dict[str, Any]] = []
        successful_steps = 0
        reused_steps = 0
        failed_steps = 0
        failed_step_codes: list[str] = []
        sorted_steps = sorted(prompt_bundle.steps, key=lambda item: item.order)
        total_steps = len(sorted_steps)
        for index, step in enumerate(sorted_steps):
            step_position = index + 1
            step_started_at = perf_counter()
            self._check_lease(lease_lost)
            resolved_prompt = self._resolve_prompt(step, request.metadata)
            step_input_digest = self._digest({"materials": material_digest, "prompt": resolved_prompt, "user": step.user_prompt, "model": step.model})
            with self.session_factory() as session:
                reusable = ContentEvaluationRepository(session).find_reusable_step(
                    task_id=message.task_id,
                    step_code=step.code,
                    input_digest=step_input_digest,
                    prompt_bundle_id=prompt_bundle_id,
                    model_version=step.model,
                )
                if reusable is not None and isinstance(reusable.result, dict):
                    aggregate[step.code] = reusable.result
                    if isinstance(reusable.token_usage, dict):
                        token_steps.append({"step_code": step.code, "token_usage": reusable.token_usage, "reused": True})
                    progress = 20 + 80 * (index + 1) / total_steps
                    with self.session_factory() as write_session:
                        with write_session.begin():
                            reused = ContentEvaluationRepository(write_session).reuse_step(
                                task_id=message.task_id,
                                execution_generation=execution_generation,
                                step_code=step.code,
                                sort_order=step.order,
                                input_digest=step_input_digest,
                                prompt_bundle_id=prompt_bundle_id,
                                model_version=step.model,
                                result=reusable.result,
                                token_usage=reusable.token_usage,
                                progress=progress,
                                aggregate_result=aggregate,
                                school_code=message.school_code,
                                operator=operator,
                            )
                            if not reused:
                                raise LeaseLostError("复用评课步骤时执行代次已经变化")
                    LOGGER.info(
                        "AI评课步骤复用完成: task_id=%s, business_task_id=%s, "
                        "classroom_id=%s, step=%s/%s, step_code=%s, step_name=%s, "
                        "progress=%.1f%%, duration_ms=%s",
                        message.task_id,
                        sanitize_log_value(business_task_id),
                        sanitize_log_value(request.classroom_id),
                        step_position,
                        total_steps,
                        step.code,
                        sanitize_log_value(step.name),
                        progress,
                        round((perf_counter() - step_started_at) * 1000),
                    )
                    reused_steps += 1
                    continue

            with self.session_factory() as session:
                with session.begin():
                    ContentEvaluationRepository(session).begin_step(
                        task_id=message.task_id,
                        execution_generation=execution_generation,
                        step_code=step.code,
                        sort_order=step.order,
                        input_digest=step_input_digest,
                        prompt_bundle_id=prompt_bundle_id,
                        model_version=step.model,
                        school_code=message.school_code,
                        operator=operator,
                    )
            LOGGER.info(
                "AI评课步骤开始: task_id=%s, business_task_id=%s, classroom_id=%s, "
                "step=%s/%s, step_code=%s, step_name=%s, model=%s, critical=%s",
                message.task_id,
                sanitize_log_value(business_task_id),
                sanitize_log_value(request.classroom_id),
                step_position,
                total_steps,
                step.code,
                sanitize_log_value(step.name),
                sanitize_log_value(step.model),
                step.critical,
            )
            try:
                result, token_usage = await self._run_step(
                    step,
                    analysis_content,
                    resolved_prompt,
                    task_id=message.task_id,
                    business_task_id=business_task_id,
                    step_position=step_position,
                    total_steps=total_steps,
                )
            except Exception as exc:
                error_result = {"error": str(exc)}
                aggregate[step.code] = error_result
                progress = 20 + 80 * (index + 1) / total_steps
                with self.session_factory() as session:
                    with session.begin():
                        ContentEvaluationRepository(session).fail_step(
                            task_id=message.task_id,
                            execution_generation=execution_generation,
                            step_code=step.code,
                            error_message=str(exc),
                            aggregate_result=aggregate,
                            progress=progress,
                            operator=operator,
                        )
                LOGGER.error(
                    "AI评课步骤失败: task_id=%s, business_task_id=%s, classroom_id=%s, "
                    "step=%s/%s, step_code=%s, step_name=%s, critical=%s, "
                    "progress=%.1f%%, duration_ms=%s, error_type=%s, error=%s",
                    message.task_id,
                    sanitize_log_value(business_task_id),
                    sanitize_log_value(request.classroom_id),
                    step_position,
                    total_steps,
                    step.code,
                    sanitize_log_value(step.name),
                    step.critical,
                    progress,
                    round((perf_counter() - step_started_at) * 1000),
                    type(exc).__name__,
                    sanitize_log_value(exc, max_length=500),
                )
                failed_steps += 1
                failed_step_codes.append(step.code)
                if step.critical:
                    raise RuntimeError(f"关键步骤 {step.code} 执行失败") from exc
                continue
            self._check_lease(lease_lost)
            aggregate[step.code] = result
            token_steps.append({"step_code": step.code, "token_usage": token_usage, "reused": False})
            progress = 20 + 80 * (index + 1) / total_steps
            with self.session_factory() as session:
                with session.begin():
                    updated = ContentEvaluationRepository(session).complete_step(
                        task_id=message.task_id,
                        execution_generation=execution_generation,
                        step_code=step.code,
                        result=result,
                        token_usage=token_usage,
                        progress=progress,
                        aggregate_result=aggregate,
                        operator=operator,
                    )
                    if not updated:
                        raise LeaseLostError("评课步骤写回时执行代次已经变化")
            LOGGER.info(
                "AI评课步骤完成: task_id=%s, business_task_id=%s, classroom_id=%s, "
                "step=%s/%s, step_code=%s, step_name=%s, progress=%.1f%%, "
                "prompt_tokens=%s, completion_tokens=%s, total_tokens=%s, duration_ms=%s",
                message.task_id,
                sanitize_log_value(business_task_id),
                sanitize_log_value(request.classroom_id),
                step_position,
                total_steps,
                step.code,
                sanitize_log_value(step.name),
                progress,
                token_usage.get("prompt_tokens", 0),
                token_usage.get("completion_tokens", 0),
                token_usage.get("total_tokens", 0),
                round((perf_counter() - step_started_at) * 1000),
            )
            successful_steps += 1
            progress_payload = {
                "taskId": business_task_id,
                "aiFormId": request.evaluation_form.ai_form_id,
                "classroomId": request.classroom_id,
                "courseName": request.evaluation_form.course_name,
                "status": "RUNNING",
                "progress": progress / 100,
                **result,
            }
            progress_callback_success = await self.progress_callback.notify(
                str(request.callback_url) if request.callback_url else None,
                progress_payload,
                request.webhook_token,
            )
            if request.callback_url:
                callback_log = LOGGER.info if progress_callback_success else LOGGER.warning
                callback_log(
                    "AI评课步骤进度回调%s: task_id=%s, business_task_id=%s, "
                    "step=%s/%s, step_code=%s, progress=%.1f%%",
                    "成功" if progress_callback_success else "失败",
                    message.task_id,
                    sanitize_log_value(business_task_id),
                    step_position,
                    total_steps,
                    step.code,
                    progress,
                )

        total_usage = {
            key: sum(int(item["token_usage"].get(key, 0)) for item in token_steps)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        final_result: dict[str, Any] = {
            "taskId": business_task_id,
            "aiFormId": request.evaluation_form.ai_form_id,
            "classroomId": request.classroom_id,
            "courseName": request.evaluation_form.course_name,
            "status": "COMPLETED",
            "progress": 1.0,
            "errorMessage": None,
            "promptVersion": prompt_version,
            "tokenUsage": {"total": total_usage, "steps": token_steps},
            "degraded": bool(missing_inputs or failed_step_codes),
            "missingInputs": missing_inputs,
            "failedSteps": failed_step_codes,
        }
        for value in aggregate.values():
            if isinstance(value, dict):
                final_result.update(value)
        with self.session_factory() as session:
            with session.begin():
                if not ContentEvaluationRepository(session).finish(
                    task_id=message.task_id,
                    execution_generation=execution_generation,
                    status="completed",
                    result=aggregate,
                    error_message=None,
                    operator=operator,
                ):
                    raise LeaseLostError("评课完成写回时执行代次已经变化")
        LOGGER.info(
            "AI评课工作流完成: task_id=%s, business_task_id=%s, classroom_id=%s, "
            "course_name=%s, prompt_version=%s, successful_steps=%s, reused_steps=%s, "
            "failed_steps=%s, total_steps=%s, "
            "prompt_tokens=%s, completion_tokens=%s, total_tokens=%s, duration_ms=%s",
            message.task_id,
            sanitize_log_value(business_task_id),
            sanitize_log_value(request.classroom_id),
            sanitize_log_value(request.evaluation_form.course_name),
            prompt_version,
            successful_steps,
            reused_steps,
            failed_steps,
            total_steps,
            total_usage["prompt_tokens"],
            total_usage["completion_tokens"],
            total_usage["total_tokens"],
            round((perf_counter() - workflow_started_at) * 1000),
        )
        return EvaluationOutcome(
            result=final_result,
            business_task_id=business_task_id,
            callback_url=str(request.callback_url) if request.callback_url else None,
            callback_token=request.webhook_token,
        )


__all__ = ["ClassEvaluationWorkflow", "EvaluationOutcome", "LeaseLostError"]
