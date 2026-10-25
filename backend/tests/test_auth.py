"""
Auth integration tests.
Run: pytest tests/ -v
"""
from fastapi.testclient import TestClient


# ── Registration ──────────────────────────────────────────────────────────────

def test_register(client: TestClient):
    resp = client.post("/api/v1/auth/register", json={
        "display_name": "Dustin",
        "email": "dustin@example.com",
        "password": "supersecret123",
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["display_name"] == "Dustin"
    assert data["email"] == "dustin@example.com"
    assert "hashed_password" not in data


def test_register_duplicate_email(client: TestClient):
    payload = {"display_name": "Al", "email": "dup@example.com", "password": "password123"}
    r1 = client.post("/api/v1/auth/register", json=payload)
    assert r1.status_code == 201, r1.text
    resp = client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 409, resp.text


# ── Login ─────────────────────────────────────────────────────────────────────

def test_login(client: TestClient):
    client.post("/api/v1/auth/register", json={
        "display_name": "Dustin", "email": "login@example.com", "password": "pass12345",
    })
    resp = client.post("/api/v1/auth/login", json={
        "email": "login@example.com", "password": "pass12345",
    })
    assert resp.status_code == 200, resp.text
    assert "access_token" in resp.json()
    assert resp.cookies.get("refresh_token") is not None


def test_login_wrong_password(client: TestClient):
    client.post("/api/v1/auth/register", json={
        "display_name": "Xy", "email": "x@example.com", "password": "correct123",
    })
    resp = client.post("/api/v1/auth/login", json={
        "email": "x@example.com", "password": "wrong",
    })
    assert resp.status_code == 401, resp.text


# ── Profile ───────────────────────────────────────────────────────────────────

def test_get_me(client: TestClient):
    client.post("/api/v1/auth/register", json={
        "display_name": "Me", "email": "me@example.com", "password": "pass12345",
    })
    login = client.post("/api/v1/auth/login", json={
        "email": "me@example.com", "password": "pass12345",
    })
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    resp = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["email"] == "me@example.com"
    assert "oauth_accounts" in data
    assert "hashed_password" not in data


def test_unauthenticated_me(client: TestClient):
    resp = client.get("/api/v1/users/me")
    assert resp.status_code == 401
