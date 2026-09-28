"""管理后台静态页面装配测试。"""

from dataclasses import dataclass

from fastapi.testclient import TestClient

from services.control_center.main import create_control_center_app


@dataclass
class FakeRuntime:
    api_key: str = "admin-ui-test-key"

    async def start(self):
        return None

    async def close(self):
        return None


def test_admin_ui_is_served_by_control_center():
    app = create_control_center_app(lambda: FakeRuntime())

    with TestClient(app) as client:
        response = client.get("/admin/")

    assert response.status_code == 200
    assert "媒体调度中心" in response.text
    assert "/admin/styles.css?v=20260924-2" in response.text
    assert "/admin/app.js?v=20260924-2" in response.text
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["expires"] == "0"
    assert 'id="tasks-pagination"' in response.text
    assert 'id="nodes-pagination"' in response.text
    assert 'id="bindings-pagination"' in response.text
    assert 'data-view="prompts"' in response.text
    assert 'id="prompt-step-tabs"' in response.text
    assert 'id="prompt-step-system"' in response.text
    assert 'id="prompt-step-user"' in response.text
    assert 'id="prompt-subtitle"' in response.text


def test_admin_ui_defaults_task_query_to_current_day():
    app = create_control_center_app(lambda: FakeRuntime())

    with TestClient(app) as client:
        response = client.get("/admin/app.js")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate"
    assert 'formatDateTimeLocal(today, "00:00")' in response.text
    assert 'formatDateTimeLocal(today, "23:59")' in response.text
    assert "setDefaultTaskDateRange();" in response.text
    assert "/api/v1/admin/content-prompts" in response.text
    assert "syncPromptEditor" in response.text
    assert "页面静态资源版本不一致，请按 Ctrl+F5 强制刷新后重试" in response.text
