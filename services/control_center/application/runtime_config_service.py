"""管理端联合发布配置和提示词模型，运行计算仍归分析服务。"""

from copy import deepcopy

from sqlalchemy import select

from media_platform.application.analysis_config import SCHEMES, validate_models
from media_platform.contracts.runtime_config import SERVICE_NODE_TYPES
from media_platform.infrastructure.database.models import (
    ContentPromptBundleModel,
    MediaNodeModel,
    MediaTaskModel,
)
from media_platform.infrastructure.database.repositories.content_prompt_bundle import (
    PromptBundleRepository,
)


class RuntimeConfigManagementService:
    def __init__(self, store):
        self.store = store

    def prompts(self, service):
        if service not in SCHEMES:
            return []
        with self.store.session_factory() as session:
            rows = session.scalars(
                select(ContentPromptBundleModel)
                .where(
                    ContentPromptBundleModel.scheme_code == SCHEMES[service],
                    ContentPromptBundleModel.status == "PUBLISHED",
                )
                .order_by(ContentPromptBundleModel.school_code)
            ).all()
            return [self.prompt_view(service, row) for row in rows]

    @staticmethod
    def prompt_view(service, row):
        models = (
            {"default": row.content["model"]}
            if service == "image-analysis"
            else {step["code"]: step["model"] for step in row.content["steps"]}
        )
        return {
            "school_code": row.school_code,
            "bundle_id": row.id,
            "expected_hash": row.content_hash,
            "models": models,
        }

    def _publish_models(self, service, session, version):
        if service not in SCHEMES:
            return
        updates = {item["school_code"]: item for item in version.prompt_updates}
        if len(updates) != len(version.prompt_updates):
            raise ValueError("学校提示词更新重复")
        rows = session.scalars(
            select(ContentPromptBundleModel)
            .where(
                ContentPromptBundleModel.scheme_code == SCHEMES[service],
                ContentPromptBundleModel.status == "PUBLISHED",
            )
            .with_for_update()
        ).all()
        if set(updates) - {row.school_code for row in rows}:
            raise ValueError("待修改学校没有已发布提示词")
        for row in rows:
            content = deepcopy(row.content)
            change = updates.get(row.school_code)
            if change:
                if (
                    change["bundle_id"] != row.id
                    or change["expected_hash"] != row.content_hash
                ):
                    raise ValueError("提示词版本已变化，请重新加载配置")
                models = change["models"]
                expected = (
                    {"default"}
                    if service == "image-analysis"
                    else {s["code"] for s in content["steps"]}
                )
                if set(models) != expected:
                    raise ValueError("模型设置必须覆盖该方案全部步骤")
                if service == "image-analysis":
                    content["model"] = models["default"]
                else:
                    for step in content["steps"]:
                        step["model"] = models[step["code"]]
            validate_models(service, content, version.content)
            if content != row.content:
                repo = PromptBundleRepository(
                    session, scheme_code=SCHEMES[service], school_code=row.school_code
                )
                draft = repo.create_draft(
                    repo.schema.model_validate(content),
                    school_code=row.school_code,
                    operator="CONFIG_ADMIN",
                )
                repo.publish(draft.id, operator="CONFIG_ADMIN")

    def publish(self, service, category, version_id):
        def validator(session, version):
            nodes = session.scalars(
                select(MediaNodeModel).where(
                    MediaNodeModel.node_type == SERVICE_NODE_TYPES[service],
                    MediaNodeModel.status == "ONLINE",
                )
            ).all()
            if any(
                not (node.capacity_config or {}).get("runtime_config_supported")
                for node in nodes
            ):
                raise ValueError("存在尚未支持热更新的在线实例，请先升级对应服务并等待心跳")
            if category == "model_connection":
                if version.base_revision == 0:
                    active = session.scalar(
                        select(MediaTaskModel.id)
                        .where(
                            MediaTaskModel.delivery_channel
                            == service.replace("-", "_").upper(),
                            MediaTaskModel.status.in_(["pending", "processing"]),
                        )
                        .limit(1)
                    )
                    if active:
                        raise ValueError("已有在途任务，请先由分析服务导入原部署配置作为首版基线")
                self._publish_models(service, session, version)

        return self.store.publish(service, category, version_id, validator)

    def content_publication_policy(self, session):
        connection = self.store.current(
            session, "content-analysis", "model_connection", lock=True
        )
        return (
            (
                lambda content: validate_models(
                    "content-analysis", content.model_dump(), connection.content
                )
            )
            if connection
            else None
        )
