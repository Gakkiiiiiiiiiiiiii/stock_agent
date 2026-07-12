from fastapi.testclient import TestClient

from app.api import app


client = TestClient(app)


def test_admin_page_available():
    response = client.get("/admin")
    assert response.status_code == 200
    assert "Admin Console" in response.text
    assert "Agent" in response.text


def test_admin_read_endpoints():
    themes = client.get("/api/v1/admin/themes")
    skills = client.get("/api/v1/admin/skills")
    docs = client.get("/api/v1/admin/docs")
    assert themes.status_code == 200
    assert skills.status_code == 200
    assert docs.status_code == 200
    assert "items" in themes.json()
    assert "items" in skills.json()
    assert "items" in docs.json()
