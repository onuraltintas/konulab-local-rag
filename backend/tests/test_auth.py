from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


def test_local_account_register_login_and_logout() -> None:
    username = f"user{uuid4().hex[:12]}"
    with TestClient(app) as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": username, "display_name": "Test öğrenci", "password": "guvenli-parola-123"},
        )
        assert registered.status_code == 201
        token = registered.json()["access_token"]
        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        logged_out = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
        expired = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        logged_in = client.post("/api/auth/login", json={"username": username, "password": "guvenli-parola-123"})

    assert me.status_code == 200
    assert me.json()["username"] == username
    assert logged_out.status_code == 204
    assert expired.status_code == 401
    assert logged_in.status_code == 200
