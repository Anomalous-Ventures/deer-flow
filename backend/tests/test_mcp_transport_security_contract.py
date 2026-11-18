"""Exercise actual MCP1.30 client guards and bounded sessions on owned loopback."""

import asyncio
import builtins
import json
import socket
import tempfile
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import httpx
import uvicorn
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Mount, Route


def bound_socket():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    sock.setblocking(False)
    return sock, f"http://127.0.0.1:{sock.getsockname()[1]}"


async def start(app, sock):
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on", timeout_graceful_shutdown=2))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Fixture server exited before startup")
                await asyncio.sleep(0.01)
    except BaseException:
        server.should_exit = True
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise
    return server, task


def errors(error):
    children = getattr(error, "exceptions", None)
    return [leaf for child in children for leaf in errors(child)] if children else [error]


async def rejected(url, status):
    try:
        async with httpx.AsyncClient(headers={"X-Maintenance-Fixture": "synthetic-only"}, follow_redirects=True, trust_env=False, timeout=3) as http:
            async with streamable_http_client(url, http_client=http) as (read, write, _):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=3)) as session:
                    await session.initialize()
    except Exception as error:
        assert any(isinstance(e, httpx.HTTPStatusError) and e.response.status_code == status for e in errors(error)), repr(error)
    else:
        raise AssertionError(f"Expected HTTP{status} rejection")


async def main():
    counts = {"cross_origin_requests": 0, "schema_requests": 0, "file_read_attempts": 0}
    async with AsyncExitStack() as stack:
        main_sock, base = bound_socket()
        stack.callback(main_sock.close)
        other_sock, other = bound_socket()
        stack.callback(other_sock.close)
        directory = stack.enter_context(tempfile.TemporaryDirectory(prefix="deerflow-mcp-guard-", dir="/tmp"))
        file = Path(directory) / "nonsecret-schema.json"
        file.write_text('{"type":"object"}\n')
        sdk = Server("owned-maintenance-fixture", version="1")
        local_schema = {"type": "object", "$defs": {"Value": {"type": "integer"}}, "properties": {"value": {"$ref": "#/$defs/Value"}}, "required": ["value"]}

        @sdk.list_tools()
        async def list_tools():
            return [types.Tool(name=name, inputSchema={"type": "object"}, outputSchema=schema) for name, schema in [("echo", local_schema), ("external_http", {"$ref": base + "/schema"}), ("external_file", {"$ref": file.as_uri()})]]

        async def call(request):
            name = request.params.name
            if name not in ("echo", "external_http", "external_file"):
                return types.ServerResult(types.CallToolResult(content=[types.TextContent(type="text", text="unknown tool")], isError=True))
            value = (request.params.arguments or {}).get("value", 1)
            return types.ServerResult(types.CallToolResult(content=[types.TextContent(type="text", text=str(value))], structuredContent={"value": value}))

        # Keep server-side validation out of this client-schema guard probe.
        sdk.request_handlers[types.CallToolRequest] = call
        manager = StreamableHTTPSessionManager(sdk, json_response=True, max_sessions=1, session_idle_timeout=2, security_settings=TransportSecuritySettings(allowed_hosts=[base.removeprefix("http://")], allowed_origins=[base]))

        @asynccontextmanager
        async def lifespan(app):
            async with manager.run():
                yield

        async def same(request):
            return RedirectResponse(base + "/mcp/", status_code=307)

        async def cross(request):
            return RedirectResponse(other + "/capture", status_code=307)

        async def schema(request):
            counts["schema_requests"] += 1
            return JSONResponse({"type": "object"})

        async def capture(request):
            counts["cross_origin_requests"] += 1
            return JSONResponse({"received": True})

        app = Starlette(routes=[Mount("/mcp", app=manager.handle_request), Route("/same", same, methods=["GET", "POST", "DELETE"]), Route("/cross", cross, methods=["GET", "POST", "DELETE"]), Route("/schema", schema)], lifespan=lifespan)
        other_app = Starlette(routes=[Route("/capture", capture, methods=["GET", "POST", "DELETE"])])
        servers = []
        try:
            servers.append(await start(other_app, other_sock))
            servers.append(await start(app, main_sock))
            async with httpx.AsyncClient(headers={"X-Maintenance-Fixture": "synthetic-only"}, follow_redirects=True, trust_env=False, timeout=3) as http:
                async with streamable_http_client(base + "/same", http_client=http) as (read, write, get_id):
                    async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=3)) as session:
                        init = await session.initialize()
                        session_id = get_id()
                        assert session_id
                        assert {t.name for t in (await session.list_tools()).tools} == {"echo", "external_http", "external_file"}
                        result = await session.call_tool("echo", {"value": 7})
                        assert not result.isError and result.structuredContent == {"value": 7}
                        assert (await session.call_tool("unknown", {})).isError
                        original_open = builtins.open

                        def guard_open(path, *args, **kwargs):
                            if isinstance(path, (str, bytes, Path)) and Path(path).resolve() == file:
                                counts["file_read_attempts"] += 1
                                raise AssertionError("External schema file read attempted")
                            return original_open(path, *args, **kwargs)

                        with patch("builtins.open", side_effect=guard_open):
                            for tool in ("external_http", "external_file"):
                                try:
                                    await session.call_tool(tool, {})
                                except RuntimeError as error:
                                    assert "schema" in str(error).lower()
                                else:
                                    raise AssertionError("External schema reference accepted: " + tool)
                        assert counts["schema_requests"] == counts["file_read_attempts"] == 0
                        assert (await session.call_tool("echo", {"value": 9})).structuredContent == {"value": 9}
                        await rejected(base + "/mcp/", 503)
                        assert len(manager._server_instances) == 1
                async with asyncio.timeout(2):
                    while manager._server_instances:
                        await asyncio.sleep(0.01)
                response = await http.post(
                    base + "/mcp/", headers={"Mcp-Session-Id": session_id, "MCP-Protocol-Version": init.protocolVersion, "Accept": "application/json, text/event-stream"}, json={"jsonrpc": "2.0", "id": 99, "method": "ping"}
                )
                assert response.status_code == 404
            await rejected(base + "/cross", 307)
            assert counts["cross_origin_requests"] == 0
        finally:
            for server, _ in servers:
                server.should_exit = True
            for _, task in servers:
                try:
                    await asyncio.wait_for(task, 5)
                except TimeoutError:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "sdk": "1.30.0",
                    "checks": [
                        "initialize/list/call",
                        "local schema references",
                        "unknown tool error",
                        "remote HTTP/file schema rejection without access",
                        "recovery after rejection",
                        "same-origin307",
                        "cross-origin307 blocked despite caller follow_redirects",
                        "max_sessions503",
                        "DELETE releases registration and stale session404",
                    ],
                    "counts": counts,
                    "limits": "Synthetic read-only protocol fixture on owned loopback; no registered MCP, real credentials, provider, deployment or server-audience configuration acceptance.",
                }
            )
        )


def test_mcp_transport_security_contract():
    asyncio.run(main())


if __name__ == "__main__":
    test_mcp_transport_security_contract()
