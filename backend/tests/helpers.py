"""Shared helpers for API tests."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

API = "/api/v1"
ADMIN = {"username": "Admin", "password": "correct horse battery"}


def client_at(app: FastAPI, ip: str = "192.168.1.20") -> TestClient:
    return TestClient(app, client=(ip, 50000))


def csrf(client: TestClient) -> dict[str, str]:
    token = client.get(f"{API}/auth/state").json()["csrf_token"]
    return {"X-CSRF-Token": token}


def do_setup(client: TestClient) -> None:
    response = client.post(f"{API}/setup", json=ADMIN, headers=csrf(client))
    assert response.status_code == 201, response.text


def admin_client(app: FastAPI) -> TestClient:
    """A client logged in as the admin (creating the admin if needed)."""
    client = client_at(app)
    if client.get(f"{API}/auth/state").json()["setup_required"]:
        do_setup(client)
    else:
        body = {"username": ADMIN["username"], "password": ADMIN["password"]}
        assert client.post(f"{API}/auth/login", json=body, headers=csrf(client)).status_code == 200
    client.headers.update(csrf(client))
    return client
