"""调用中心图像提示词 API 与旧评课 API 的方案隔离。"""

from dataclasses import dataclass

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from media_platform.application.image_prompt_service import (
    ImagePromptManagementService,
    ImagePromptPolicy,
    ensure_image_prompt_bootstrap,
)
from media_platform.infrastructure.database.models.content_prompt_bundle import (
    ContentPromptBundleModel,
)
from services.control_center.main import create_control_center_app
from services.image_analysis.prompt_defaults import load_bootstrap_prompt_bundle


@dataclass
class FakeRuntime:
    image_prompt_service: ImagePromptManagementService | None
    api_key: str = "image-admin-key"

    async def start(self):
        return None

    async def close(self):
        return None


def test_image_prompt_api_lifecycle_scope_and_auth(tmp_path):
    engine = sa.create_engine(
        f"sqlite:///{(tmp_path / 'admin-image-prompts.db').as_posix()}"
    )
    ContentPromptBundleModel.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    policy = ImagePromptPolicy(
        allowed_models=frozenset({"test-vision"}),
        output_schema_versions=frozenset({"image-metrics.v1"}),
        metric_definition_versions=frozenset({"classroom-behavior.v1"}),
    )
    service = ImagePromptManagementService(factory, policy)
    seed = load_bootstrap_prompt_bundle("test-vision")
    initial = ensure_image_prompt_bootstrap(factory, lambda: seed, policy)
    app = create_control_center_app(lambda: FakeRuntime(service))
    headers = {"X-API-Key": "image-admin-key"}
    root = "/api/v1/admin/image-prompts"
    try:
        with TestClient(app) as client:
            assert client.get(root).status_code == 401
            assert client.get(root, headers=headers).json()[0]["id"] == initial.id
            assert (
                client.get(f"{root}?school_code=school-a", headers=headers).json() == []
            )
            forbidden = client.post(
                f"{root}/{initial.id}/clone",
                headers=headers,
                json={"school_code": "school-a"},
            )
            assert forbidden.status_code == 404

            created = client.post(
                f"{root}/drafts",
                headers=headers,
                json={"school_code": "school-a", "content": seed.to_payload()},
            )
            assert created.status_code == 201
            draft = created.json()
            assert draft["content"]["prompt"] == seed.prompt
            assert draft["updated_by"] == "ADMIN_API_KEY"
            assert (
                client.get(f"{root}?school_code=GLOBAL", headers=headers).json()[0][
                    "id"
                ]
                == initial.id
            )

            changed = dict(draft["content"], prompt=seed.prompt + "\n版本二")
            stale = client.put(
                f"{root}/{draft['id']}",
                headers=headers,
                json={
                    "school_code": "school-a",
                    "expected_hash": "0" * 64,
                    "content": changed,
                },
            )
            assert stale.status_code == 409
            saved = client.put(
                f"{root}/{draft['id']}",
                headers=headers,
                json={
                    "school_code": "school-a",
                    "expected_hash": draft["content_hash"],
                    "content": changed,
                },
            )
            assert saved.status_code == 200
            published = client.post(
                f"{root}/{draft['id']}/publish",
                headers=headers,
                json={
                    "school_code": "school-a",
                    "expected_hash": saved.json()["content_hash"],
                },
            )
            assert published.status_code == 200
            assert published.json()["status"] == "PUBLISHED"
            assert (
                client.put(
                    f"{root}/{draft['id']}",
                    headers=headers,
                    json={
                        "school_code": "school-a",
                        "expected_hash": saved.json()["content_hash"],
                        "content": changed,
                    },
                ).status_code
                == 409
            )
            assert (
                client.post(
                    f"{root}/{draft['id']}/publish",
                    headers=headers,
                    json={
                        "school_code": "GLOBAL",
                        "expected_hash": saved.json()["content_hash"],
                    },
                ).status_code
                == 404
            )
            assert (
                client.post(
                    f"{root}/{initial.id}/publish",
                    headers=headers,
                    json={
                        "school_code": "GLOBAL",
                        "expected_hash": initial.content_hash,
                    },
                ).status_code
                == 200
            )
    finally:
        engine.dispose()


def test_image_prompt_api_unconfigured_returns_503():
    app = create_control_center_app(lambda: FakeRuntime(None))
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/admin/image-prompts", headers={"X-API-Key": "image-admin-key"}
        )
    assert response.status_code == 503
