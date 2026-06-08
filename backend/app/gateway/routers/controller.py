"""Ariadne proxy router.

Exposes ``/api/v1/controller/*`` on the deer-flow gateway and forwards
the request to the in-cluster Ariadne service. This lets deer-flow
act as the operator-facing UI for Ariadne without bypassing
the gateway's auth + audit layer.

Safety:
  * Disabled by default. Set ``DEER_FLOW_CONTROLLER_PROXY_ENABLED=1``
    to enable. When disabled, every endpoint returns 404 so the
    surface is invisible.
  * Admin-only. ``system_role != "admin"`` -> 403.
  * Strict path allow-list. Only the Ariadne routes we explicitly
    forward are reachable; anything else returns 404.
  * Read-mostly: GET + POST are forwarded. PUT/PATCH/DELETE are
    rejected at the router until a named use-case proves they're
    needed.

Configuration:
  * ``DEER_FLOW_ARIADNE_URL`` -- upstream base URL.
    Default ``http://ariadne.llm.svc.cluster.local:8096``.
  * ``DEER_FLOW_CONTROLLER_PROXY_TIMEOUT_SECONDS`` -- per-request
    timeout (default 30).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from app.gateway.deps import get_current_user_from_request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/controller", tags=["controller"])

DEFAULT_ARIADNE_URL = "http://ariadne.llm.svc.cluster.local:8096"
DEFAULT_TIMEOUT = 30.0

# Allow-listed Ariadne paths. Each entry is (method, suffix-or-prefix,
# is_prefix). The "suffix" is the portion AFTER /api/v1/controller.
# is_prefix=True matches sub-paths so /staple/<run_id>/logs maps to
# the upstream /api/staple/<run_id>/logs.
_ALLOW: tuple[tuple[str, str, bool], ...] = (
    # Dev loop control
    ("POST", "/dev-loop/start", False),
    ("POST", "/dev-loop/stop", False),
    ("GET", "/dev-loop/status", False),
    # Kanban board
    ("GET", "/kanban/board", False),
    ("POST", "/kanban/cards", False),
    ("GET", "/kanban/cards", True),
    # Staple (test-gen / quality gates)
    ("GET", "/staple", True),
    ("POST", "/staple", True),
    # Health probe
    ("GET", "/health", False),
)


def _is_enabled() -> bool:
    return os.environ.get("DEER_FLOW_CONTROLLER_PROXY_ENABLED", "").strip() == "1"


def _ariadne_base_url() -> str:
    raw = os.environ.get("DEER_FLOW_ARIADNE_URL", "").strip()
    return (raw or DEFAULT_ARIADNE_URL).rstrip("/")


def _timeout() -> float:
    raw = os.environ.get("DEER_FLOW_CONTROLLER_PROXY_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT


def _path_allowed(method: str, suffix: str) -> bool:
    method_u = method.upper()
    for allowed_method, pattern, is_prefix in _ALLOW:
        if allowed_method != method_u:
            continue
        if is_prefix:
            if suffix == pattern or suffix.startswith(pattern + "/"):
                return True
        elif suffix == pattern:
            return True
    return False


def _map_to_upstream(suffix: str) -> str:
    """Map deer-flow /api/v1/controller/<x> to Ariadne's path namespace.

    Ariadne's actual mount points are:
      * /dev-loop/*  (no /api prefix)
      * /api/kanban/*
      * /api/staple/*
      * /health
    """
    if suffix.startswith("/dev-loop") or suffix == "/health":
        return suffix
    if suffix.startswith("/kanban") or suffix.startswith("/staple"):
        return "/api" + suffix
    return suffix


_HOP_BY_HOP: frozenset[str] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "content-length",
        "host",
    }
)


def _filter_request_headers(headers: Iterable[tuple[bytes, bytes]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in headers:
        key = name.decode("latin-1").lower()
        if key in _HOP_BY_HOP:
            continue
        # Strip the deer-flow auth cookie before forwarding upstream.
        # Ariadne does not consume deer-flow's JWT; passing it leaks the
        # cookie to a non-issuing service.
        if key == "cookie":
            continue
        if key == "authorization":
            continue
        out[name.decode("latin-1")] = value.decode("latin-1")
    return out


def _filter_response_headers(headers: httpx.Headers) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in _HOP_BY_HOP:
            continue
        out[key] = value
    return out


@router.api_route(
    "/{suffix:path}",
    methods=["GET", "POST"],
)
async def proxy(suffix: str, request: Request) -> Response:
    if not _is_enabled():
        raise HTTPException(status_code=404, detail="controller proxy disabled")

    user = await get_current_user_from_request(request)
    if user.system_role != "admin":
        raise HTTPException(status_code=403, detail="controller endpoints require admin role")

    # Normalize: the FastAPI path matcher delivers ``suffix`` without
    # a leading slash. Re-prepend so allow-list patterns stay readable.
    normalized = "/" + suffix.lstrip("/")
    if not _path_allowed(request.method, normalized):
        raise HTTPException(status_code=404, detail="path not allowed")

    upstream_path = _map_to_upstream(normalized)
    upstream_url = f"{_ariadne_base_url()}{upstream_path}"

    body = await request.body()
    headers = _filter_request_headers(request.headers.raw)

    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            upstream = await client.request(
                request.method,
                upstream_url,
                params=dict(request.query_params),
                headers=headers,
                content=body if body else None,
            )
    except httpx.TimeoutException:
        logger.warning(
            "controller_proxy.upstream_timeout",
            extra={"path": upstream_path, "method": request.method},
        )
        raise HTTPException(status_code=504, detail="Ariadne upstream timeout")
    except httpx.HTTPError as exc:
        logger.error(
            "controller_proxy.upstream_error",
            extra={"path": upstream_path, "error": str(exc)},
        )
        raise HTTPException(status_code=502, detail="Ariadne upstream error")

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_filter_response_headers(upstream.headers),
        media_type=upstream.headers.get("content-type"),
    )
