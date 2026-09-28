from dataclasses import dataclass

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from media_platform.application.content_prompt_service import PromptManagementService
from media_platform.contracts.content_evaluation import PromptBundleContent, PromptStepDefinition
from media_platform.infrastructure.database.models.content_prompt_bundle import (
    ContentPromptBundleModel,
)
from services.control_center.main import create_control_center_app


def _bundle(label: str) -> PromptBundleContent:
    codes = (
        "CLASSROOM_SUMMARY",
        "FREQUENCY_ANALYSIS",
        "KEYEVENT_ANALYSIS",
        "CONTENT_SUMMARY",
        "QA_ANALYSIS",
        "TEACHING_METHOD",
        "KNOWLEDGE_GRAPH",
        "CLASSROOM_SCORE",
    )
    return PromptBundleContent(
        subtitle_edit_prompt=f"{label}-字幕整理",
        steps=[
            PromptStepDefinition(
                code=code,
                name=f"{label}-{code}",
                order=index,
                system_prompt=f"{label}-{code}-系统提示词",
                user_prompt=f"{label}-{code}-用户提示词",
                model="test-model",
                critical=code == "CLASSROOM_SCORE",
            )
            for index, code in enumerate(codes, start=1)
        ],
    )


@dataclass
class FakeRuntime:
    content_prompt_service: PromptManagementService
    api_key: str = "prompt-admin-key"

    async def start(self):
        return None

    async def close(self):
        return None


def test_control_center_manages_prompt_versions_directly_in_database(tmp_path):
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'control-center-prompts.db').as_posix()}")
    ContentPromptBundleModel.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    prompt_service = PromptManagementService(factory)
    published = prompt_service.publish(
        prompt_service.create_draft(
            _bundle("v1"),
            school_code="GLOBAL",
            operator="seed",
        ).id,
        operator="seed",
    )
    app = create_control_center_app(lambda: FakeRuntime(prompt_service))
    headers = {"X-API-Key": "prompt-admin-key"}

    try:
        with TestClient(app) as client:
            listed = client.get(
                "/api/v1/admin/content-prompts?school_code=GLOBAL",
                headers=headers,
            )
            cloned = client.post(
                f"/api/v1/admin/content-prompts/{published.id}/clone",
                headers=headers,
                json={"school_code": "GLOBAL", "operator": "editor"},
            )
            draft = cloned.json()
            content = draft["content"]
            content["steps"][0]["system_prompt"] = "管理页替换后的第一步提示词"
            saved = client.put(
                f"/api/v1/admin/content-prompts/{draft['id']}",
                headers=headers,
                json={"operator": "editor", "content": content},
            )
            republished = client.post(
                f"/api/v1/admin/content-prompts/{draft['id']}/publish",
                headers=headers,
                json={"operator": "publisher"},
            )

        assert listed.status_code == 200
        assert listed.json()[0]["status"] == "PUBLISHED"
        assert cloned.status_code == 201
        assert draft["status"] == "DRAFT"
        assert saved.status_code == 200
        assert saved.json()["content"]["steps"][0]["system_prompt"] == "管理页替换后的第一步提示词"
        assert republished.status_code == 200
        assert republished.json()["status"] == "PUBLISHED"
        assert prompt_service.get_published("GLOBAL").id == draft["id"]
    finally:
        engine.dispose()
