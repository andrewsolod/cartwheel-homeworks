"""HW2 authentication tests for the session endpoints. Offline: no Langfuse, Docker, or model key."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server import app as server_app


@pytest.fixture
def client(world: dict, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    server_app._SESSIONS.clear()

    async def agent_must_not_run(*args, **kwargs):
        raise AssertionError("the agent ran for an unauthorized request")

    monkeypatch.setattr(server_app.Runner, "run", agent_must_not_run)
    return TestClient(server_app.app)


def test_session_rejects_role_that_differs_from_database(client: TestClient) -> None:
    # User 1 is a shopper in the seeded world; claiming merchant must fail.
    response = client.post("/sessions", json={"user_id": 1, "role": "merchant"})

    assert response.status_code == 403
    assert server_app._SESSIONS == {}


def test_token_for_one_session_cannot_authorize_another(client: TestClient) -> None:
    first = client.post("/sessions", json={"user_id": 1, "role": "shopper"}).json()
    second = client.post("/sessions", json={"user_id": 9002, "role": "merchant"}).json()

    response = client.post(
        f"/sessions/{second['session_id']}/messages",
        headers={"Authorization": f"Bearer {first['token']}"},
        json={"message": "Show my recent orders."},
    )

    assert response.status_code == 403
