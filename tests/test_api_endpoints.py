import pytest


@pytest.mark.asyncio
async def test_api_health(api_client):
    response = await api_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "helm-api"


@pytest.mark.asyncio
async def test_api_projects_and_tasks_flow(api_client, temp_repo):
    # 1. Create project
    proj_resp = await api_client.post(
        "/api/projects",
        json={
            "name": "Test Calculator App",
            "repo_path": temp_repo,
            "default_branch": "main",
        },
    )
    assert proj_resp.status_code == 201
    proj_data = proj_resp.json()
    project_id = proj_data["id"]

    # 2. List projects
    list_resp = await api_client.get("/api/projects")
    assert list_resp.status_code == 200
    assert any(p["id"] == project_id for p in list_resp.json())

    # 3. Create task
    task_resp = await api_client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "prompt": "Fix calculator bug",
            "mode": "autonomous",
        },
    )
    assert task_resp.status_code == 202
    task_data = task_resp.json()
    task_id = task_data["id"]
    assert task_id

    # 4. Get task
    get_task_resp = await api_client.get(f"/api/tasks/{task_id}")
    assert get_task_resp.status_code == 200
    assert get_task_resp.json()["id"] == task_id

    # 5. Get project graph
    graph_resp = await api_client.get(f"/api/projects/{project_id}/graph")
    assert graph_resp.status_code == 200
    assert "nodes" in graph_resp.json()
