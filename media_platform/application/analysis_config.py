"""分析任务的持久配置引用；不包含推理或服务私有业务。"""

from sqlalchemy import select
import os

from media_platform.application.image_prompt_service import ImagePromptPolicy
from media_platform.contracts.content_evaluation import PromptBundleContent
from media_platform.contracts.image_analysis import (
    IMAGE_PROMPT_SCHEME,
    ImagePromptBundleContent,
)
from media_platform.infrastructure.database.models import (
    ContentEvaluationRecordModel,
    ContentPromptBundleModel,
    MediaTaskModel,
    ServiceConfigVersionModel,
)
from media_platform.infrastructure.database.repositories.content_prompt_bundle import (
    PROMPT_SCHEME_CODE,
    PromptBundleRepository,
    prompt_content_hash,
)


SCHEMES = {
    "image-analysis": IMAGE_PROMPT_SCHEME,
    "content-analysis": PROMPT_SCHEME_CODE,
}


def prompt_models(service, content):
    if service == "image-analysis":
        return {ImagePromptBundleContent.model_validate(content).model_dump()["model"]}
    return {step.model for step in PromptBundleContent.model_validate(content).steps}


def validate_models(service, content, connection):
    missing = prompt_models(service, content) - set(connection["allowed_models"])
    if missing:
        raise ValueError("提示词模型不在连接配置允许名单中")


def prompt_reference(row):
    return {
        "id": row.id,
        "schoolCode": row.school_code,
        "version": row.version,
        "contentHash": row.content_hash,
    }


def current_content_prompt(session, school_code):
    row = PromptBundleRepository(session).get_published(school_code)
    if row is None:
        raise ValueError("没有已发布的内容分析提示词")
    return row


class AnalysisConfigReader:
    def __init__(self, store, service):
        self.store, self.service = store, service

    def prepare(self, session, school_code, params):
        """调用中心创建事务内调用；先锁配置主行，再选择提示词。"""
        result = dict(params)
        result.pop("_runtimeConfig", None)  # 客户端不能指定内部配置/凭据引用。
        connection = self.store.current(
            session, self.service, "model_connection", lock=True
        )
        if self.service == "content-analysis":
            prompt = current_content_prompt(session, school_code)
            result["_promptBundle"] = prompt_reference(prompt)
            if connection:
                validate_models(self.service, prompt.content, connection.content)
        if connection:
            result["_runtimeConfig"] = {"connection_id": connection.id}
        return result

    def task_params(self, task_id, generation):
        """从任务事实读取，旧消息不可覆盖；历史任务一次锁定首版连接和原评课版本。"""
        with self.store.session_factory() as session, session.begin():
            task = session.scalar(
                select(MediaTaskModel)
                .where(MediaTaskModel.id == task_id)
                .with_for_update()
            )
            if (
                task is None
                or task.execution_generation != generation
                or task.status != "processing"
            ):
                raise ValueError("配置锁定时任务执行代次已变化")
            params = dict(task.params)
            changed = False
            baseline = None
            reference = params.get("_runtimeConfig")
            if reference:
                baseline = session.get(
                    ServiceConfigVersionModel, reference["connection_id"]
                )
            if "_runtimeConfig" not in params:
                head = self.store.head(session, self.service, "model_connection")
                if head:
                    baseline = session.scalar(
                        select(ServiceConfigVersionModel).where(
                            ServiceConfigVersionModel.config_id == head.id,
                            ServiceConfigVersionModel.revision == 1,
                            ServiceConfigVersionModel.status == "PUBLISHED",
                        )
                    )
                    if baseline:
                        params["_runtimeConfig"] = {"connection_id": baseline.id}
                        changed = True
            if self.service == "content-analysis" and "_promptBundle" not in params:
                record = session.scalar(
                    select(ContentEvaluationRecordModel).where(
                        ContentEvaluationRecordModel.task_id == task.id
                    )
                )
                prompt = (
                    session.get(ContentPromptBundleModel, record.prompt_bundle_id)
                    if record and record.prompt_bundle_id
                    else None
                )
                if prompt is None and baseline:
                    candidates = {p["school_code"]: p for p in baseline.prompt_updates}
                    previous = candidates.get(task.school_code) or candidates.get(
                        "GLOBAL"
                    )
                    if previous:
                        prompt = session.get(
                            ContentPromptBundleModel, previous["bundle_id"]
                        )
                prompt = prompt or current_content_prompt(session, task.school_code)
                params["_promptBundle"] = prompt_reference(prompt)
                changed = True
            if changed:
                task.params = params
            return params

    def connection(self, params):
        ref = params.get("_runtimeConfig")
        if not ref:
            return None
        snapshot = self.store.snapshot(
            self.service, "model_connection", ref["connection_id"]
        )
        if snapshot is None:
            raise ValueError("任务锁定的模型连接不存在")
        return snapshot


class DatabaseImagePromptPolicy:
    """新发布使用当前名单，历史任务由锁定连接名单校验。"""

    def __init__(self, store, fallback_models):
        self.store = store
        self.fallback_models = frozenset(fallback_models)

    def validate_in_session(self, content, session, *, historical=False):
        row = self.store.current(session, "image-analysis", "model_connection")
        allowed = (
            frozenset(row.content["allowed_models"]) if row else self.fallback_models
        )
        policy = ImagePromptPolicy(
            allowed_models=frozenset({content.model}) if historical else allowed,
            output_schema_versions=frozenset({"image-metrics.v1"}),
            metric_definition_versions=frozenset({"classroom-behavior.v1"}),
        )
        policy.validate(content)

    def validate(self, content):
        with self.store.session_factory() as session:
            self.validate_in_session(content, session)

    def prepare_session(self, session):
        self.store.head(session, "image-analysis", "model_connection", lock=True)


def locked_content_prompt(session, reference, school_code):
    row = session.get(ContentPromptBundleModel, reference["id"])
    if (
        row is None
        or row.scheme_code != PROMPT_SCHEME_CODE
        or row.school_code not in {school_code, "GLOBAL"}
        or row.published_at is None
        or row.content_hash != reference["contentHash"]
        or row.version != reference["version"]
        or prompt_content_hash(row.content) != row.content_hash
    ):
        raise ValueError("任务锁定提示词无效")
    return row


def initialize_analysis_config(store, service, connection, performance, api_key):
    """显式单实例导入开关；普通启动只读数据库，不覆盖部署间差异。"""
    if os.getenv("RUNTIME_CONFIG_BOOTSTRAP", "false").lower() not in {
        "1",
        "true",
        "yes",
    }:
        return
    with store.session_factory() as session:
        prompts = session.scalars(
            select(ContentPromptBundleModel).where(
                ContentPromptBundleModel.scheme_code == SCHEMES[service],
                ContentPromptBundleModel.status == "PUBLISHED",
            )
        ).all()
        updates = [
            {
                "school_code": row.school_code,
                "bundle_id": row.id,
                "expected_hash": row.content_hash,
                "models": (
                    {"default": row.content["model"]}
                    if service == "image-analysis"
                    else {s["code"]: s["model"] for s in row.content["steps"]}
                ),
            }
            for row in prompts
        ]
        if service == "content-analysis":
            connection = {
                **connection,
                "allowed_models": sorted(
                    set(connection["allowed_models"])
                    | {
                        model
                        for row in prompts
                        for model in prompt_models(service, row.content)
                    }
                ),
            }
    store.bootstrap(service, "model_connection", connection, api_key, updates)
    store.bootstrap(service, "performance", performance)
