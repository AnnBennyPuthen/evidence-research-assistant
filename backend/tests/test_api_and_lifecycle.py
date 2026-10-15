import asyncio

from fastapi.testclient import TestClient

from app.config import Settings
from app.models import ResearchRequest
from app.repository import Repository
from app.runner import RunManager


def test_demo_api_completes_with_events_and_exports(tmp_path, monkeypatch):
    import app.main as main

    monkeypatch.setattr(
        main, "settings", Settings(app_mode="demo", database_path=str(tmp_path / "api.sqlite"))
    )
    monkeypatch.setattr(main, "repository", Repository(str(tmp_path / "api.sqlite")))
    monkeypatch.setattr(main, "manager", RunManager(main.settings, main.repository))
    with TestClient(main.app) as client:
        response = client.post(
            "/api/research",
            json={
                "question": "How does graph orchestration compare with a linear workflow?",
                "depth": "quick",
                "preferred_domains": [],
            },
        )
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        result = None
        for _ in range(50):
            detail = client.get(f"/api/research/{run_id}")
            result = detail.json()["result"]
            if result["status"] == "completed":
                break
            import time

            time.sleep(0.02)
        assert result["status"] == "completed", result
        assert result["report"]["demo_label"] == "Demo — synthetic evidence; no live research"
        event_response = client.get(f"/api/research/{run_id}/events?cursor=0")
        assert event_response.status_code == 200
        assert any(event["kind"] == "report" for event in event_response.json()["events"])
        assert client.get(f"/api/research/{run_id}/export?format=markdown").text.startswith(
            "# Evidence-Based Research Report"
        )
        assert client.get(f"/api/research/{run_id}/export?format=json").json()["run_id"] == run_id


def test_live_mode_missing_keys_is_actionable_503(monkeypatch):
    import app.main as main

    monkeypatch.setattr(
        main, "settings", Settings(app_mode="live", openai_api_key=None, tavily_api_key=None)
    )
    with TestClient(main.app) as client:
        response = client.post(
            "/api/research",
            json={
                "question": "How does graph orchestration compare with a linear workflow?",
                "depth": "quick",
            },
        )
    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["detail"]["message"]


def test_cancel_persists_cancelled_state_and_restart_marks_running_jobs_interrupted(tmp_path):
    async def scenario():
        repo = Repository(str(tmp_path / "lifecycle.sqlite"))
        manager = RunManager(
            Settings(app_mode="demo", database_path=str(tmp_path / "lifecycle.sqlite")), repo
        )
        run_id = await manager.start(
            ResearchRequest(question="How does graph orchestration compare with a linear workflow?")
        )
        await asyncio.sleep(0)
        assert await manager.cancel(run_id)
        task = manager.tasks.get(run_id)
        if task:
            await asyncio.gather(task, return_exceptions=True)
        assert repo.get_run(run_id)["status"] == "cancelled"
        repo.create_run(
            "run_restart",
            ResearchRequest(
                question="How does graph orchestration compare with a linear workflow?"
            ).model_dump(),
        )
        repo.save_result("run_restart", "running", {"request": {}})
        repo.interrupt_unfinished()
        assert repo.get_run("run_restart")["status"] == "interrupted"

    asyncio.run(scenario())
