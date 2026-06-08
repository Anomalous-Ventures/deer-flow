"""Unit tests for the Ariadne proxy router.

Covers the contract surface that future Ariadne integrations will rely on:

* Disabled by default (env unset -> 404 on every endpoint).
* Admin-only gate (system_role != "admin" -> 403).
* Strict allow-list (unknown path -> 404, even when enabled + admin).
* Path mapping (``/kanban/*`` / ``/staple/*`` get ``/api`` prefix;
  ``/dev-loop/*`` and ``/health`` pass through).
* Forward path: status code, body, and content-type round-trip.
* Cookie + Authorization request headers are stripped before forward.
* Upstream timeout -> 504. Upstream transport error -> 502.
"""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers import controller


def _admin_user() -> User:
    return User(
        email="admin-test@example.com",
        password_hash="x",
        system_role="admin",
        id=uuid4(),
    )


def _non_admin_user() -> User:
    return User(
        email="user-test@example.com",
        password_hash="x",
        system_role="user",
        id=uuid4(),
    )


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(controller.router)
    return app


def _install_user(monkeypatch: pytest.MonkeyPatch, user: User | None) -> None:
    async def fake_get_user(_request):
        if user is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=401, detail="no user")
        return user

    monkeypatch.setattr(controller, "get_current_user_from_request", fake_get_user)


def _install_upstream(
    monkeypatch: pytest.MonkeyPatch,
    handler,
) -> dict:
    """Replace ``httpx.AsyncClient`` inside the controller module with one
    backed by ``MockTransport`` driven by *handler*. Returns a dict that
    captures the request the handler observed for assertion.
    """
    captured: dict = {}

    def _wrapped_handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["content"] = request.content
        return handler(request)

    transport = httpx.MockTransport(_wrapped_handler)

    class _PatchedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(controller.httpx, "AsyncClient", _PatchedClient)
    return captured


def test_disabled_by_default_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEER_FLOW_CONTROLLER_PROXY_ENABLED", raising=False)
    _install_user(monkeypatch, _admin_user())

    with TestClient(_build_app()) as client:
        resp = client.get("/api/v1/controller/health")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "controller proxy disabled"


def test_non_admin_blocked_with_403(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEER_FLOW_CONTROLLER_PROXY_ENABLED", "1")
    _install_user(monkeypatch, _non_admin_user())

    with TestClient(_build_app()) as client:
        resp = client.get("/api/v1/controller/health")

    assert resp.status_code == 403


def test_path_not_in_allow_list_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEER_FLOW_CONTROLLER_PROXY_ENABLED", "1")
    _install_user(monkeypatch, _admin_user())

    with TestClient(_build_app()) as client:
        resp = client.get("/api/v1/controller/admin/secret")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "path not allowed"


def test_disallowed_method_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    # Allow list has only GET for /dev-loop/status; POST must 404.
    monkeypatch.setenv("DEER_FLOW_CONTROLLER_PROXY_ENABLED", "1")
    _install_user(monkeypatch, _admin_user())

    with TestClient(_build_app()) as client:
        resp = client.post("/api/v1/controller/dev-loop/status")

    assert resp.status_code == 404


def test_health_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEER_FLOW_CONTROLLER_PROXY_ENABLED", "1")
    _install_user(monkeypatch, _admin_user())

    captured = _install_upstream(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            json={"status": "ok"},
            headers={"content-type": "application/json"},
        ),
    )

    with TestClient(_build_app()) as client:
        resp = client.get("/api/v1/controller/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert captured["url"].endswith("/health")
    assert captured["method"] == "GET"


def test_kanban_path_gets_api_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEER_FLOW_CONTROLLER_PROXY_ENABLED", "1")
    _install_user(monkeypatch, _admin_user())

    captured = _install_upstream(
        monkeypatch,
        lambda req: httpx.Response(200, json={"cards": []}),
    )

    with TestClient(_build_app()) as client:
        resp = client.get("/api/v1/controller/kanban/board")

    assert resp.status_code == 200
    assert captured["url"].endswith("/api/kanban/board")


def test_staple_prefix_path_maps_with_api_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEER_FLOW_CONTROLLER_PROXY_ENABLED", "1")
    _install_user(monkeypatch, _admin_user())

    captured = _install_upstream(
        monkeypatch,
        lambda req: httpx.Response(200, json={"runs": []}),
    )

    with TestClient(_build_app()) as client:
        resp = client.get("/api/v1/controller/staple/abc123/logs")

    assert resp.status_code == 200
    assert captured["url"].endswith("/api/staple/abc123/logs")


def test_dev_loop_path_passes_through_without_api_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEER_FLOW_CONTROLLER_PROXY_ENABLED", "1")
    _install_user(monkeypatch, _admin_user())

    captured = _install_upstream(
        monkeypatch,
        lambda req: httpx.Response(200, json={"started": True}),
    )

    with TestClient(_build_app()) as client:
        resp = client.post(
            "/api/v1/controller/dev-loop/start",
            json={"repo": "foo/bar"},
        )

    assert resp.status_code == 200
    assert captured["url"].endswith("/dev-loop/start")
    assert captured["method"] == "POST"
    assert b"foo/bar" in captured["content"]


def test_auth_and_cookie_headers_stripped_before_forward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEER_FLOW_CONTROLLER_PROXY_ENABLED", "1")
    _install_user(monkeypatch, _admin_user())

    captured = _install_upstream(
        monkeypatch,
        lambda req: httpx.Response(200, json={"ok": True}),
    )

    with TestClient(_build_app()) as client:
        resp = client.get(
            "/api/v1/controller/health",
            headers={
                "Authorization": "Bearer secret-token",
                "Cookie": "access_token=secret-jwt",
                "X-Trace-Id": "trace-1",
            },
        )

    assert resp.status_code == 200
    fwd = {k.lower() for k in captured["headers"]}
    assert "authorization" not in fwd
    assert "cookie" not in fwd
    # Non-sensitive headers should still propagate.
    assert "x-trace-id" in fwd


def test_upstream_timeout_returns_504(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEER_FLOW_CONTROLLER_PROXY_ENABLED", "1")
    _install_user(monkeypatch, _admin_user())

    def _raise_timeout(req: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("simulated timeout", request=req)

    _install_upstream(monkeypatch, _raise_timeout)

    with TestClient(_build_app()) as client:
        resp = client.get("/api/v1/controller/health")

    assert resp.status_code == 504
    assert resp.json()["detail"] == "Ariadne upstream timeout"


def test_upstream_transport_error_returns_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEER_FLOW_CONTROLLER_PROXY_ENABLED", "1")
    _install_user(monkeypatch, _admin_user())

    def _raise_conn_err(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=req)

    _install_upstream(monkeypatch, _raise_conn_err)

    with TestClient(_build_app()) as client:
        resp = client.get("/api/v1/controller/health")

    assert resp.status_code == 502
    assert resp.json()["detail"] == "Ariadne upstream error"


def test_upstream_error_status_propagated(monkeypatch: pytest.MonkeyPatch) -> None:
    # Upstream 5xx is NOT a transport error -- pass the body + status through.
    monkeypatch.setenv("DEER_FLOW_CONTROLLER_PROXY_ENABLED", "1")
    _install_user(monkeypatch, _admin_user())

    _install_upstream(
        monkeypatch,
        lambda req: httpx.Response(503, json={"error": "Ariadne busy"}),
    )

    with TestClient(_build_app()) as client:
        resp = client.get("/api/v1/controller/health")

    assert resp.status_code == 503
    assert resp.json() == {"error": "Ariadne busy"}


def test_query_params_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEER_FLOW_CONTROLLER_PROXY_ENABLED", "1")
    _install_user(monkeypatch, _admin_user())

    captured = _install_upstream(
        monkeypatch,
        lambda req: httpx.Response(200, json={"cards": []}),
    )

    with TestClient(_build_app()) as client:
        resp = client.get(
            "/api/v1/controller/kanban/cards/abc",
            params={"limit": "20", "state": "in_progress"},
        )

    assert resp.status_code == 200
    url = captured["url"]
    assert "limit=20" in url
    assert "state=in_progress" in url


def test_custom_ariadne_url_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEER_FLOW_CONTROLLER_PROXY_ENABLED", "1")
    monkeypatch.setenv("DEER_FLOW_ARIADNE_URL", "http://alt-ariadne.example:9000/")
    _install_user(monkeypatch, _admin_user())

    captured = _install_upstream(
        monkeypatch,
        lambda req: httpx.Response(200, json={"ok": True}),
    )

    with TestClient(_build_app()) as client:
        resp = client.get("/api/v1/controller/health")

    assert resp.status_code == 200
    assert captured["url"].startswith("http://alt-ariadne.example:9000/")
