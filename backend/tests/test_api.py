"""API 路由测试

测试 FastAPI 端点。
"""

class TestHealthCheck:
    """健康检查测试"""

    def test_health_check(self, client):
        """测试健康检查端点"""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "version" in data


def test_only_platform_tasks_are_registered():
    from app.tasks.celery_app import celery_app

    celery_app.loader.import_default_modules()
    assert {name for name in celery_app.tasks if name.startswith("app.")} == {"app.platform.worker.execute_job"}
