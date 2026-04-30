"""anyrun MCP Server — 让 Hermes/Claude Desktop 等 MCP 客户端直接调用 anyrun

启动方式:
    python -m anyrun.mcp_server

Hermes 配置 (config.yaml):
    mcp_servers:
      anyrun:
        command: "python3"
        args: ["-m", "anyrun.mcp_server"]

暴露的工具:
    - sandbox_run: 在 Docker 沙箱中执行 Python 代码
    - trace_list: 列出执行轨迹
    - trace_get: 获取单条轨迹详情
    - trace_stats: 获取统计信息
"""

import asyncio
import json
import sys
import os
from typing import Any

from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
    ServerCapabilities,
    ToolsCapability,
)


def _discover_docker_host() -> None:
    """智能发现 Docker socket 路径，兼容 macOS 和 Linux"""
    if "DOCKER_HOST" in os.environ:
        return
    candidates = [
        os.path.expanduser("~/.docker/run/docker.sock"),
        "/var/run/docker.sock",
    ]
    for sock in candidates:
        if os.path.exists(sock):
            os.environ["DOCKER_HOST"] = f"unix://{sock}"
            return


async def handle_sandbox_run(arguments: dict) -> list[TextContent]:
    """在 Docker 沙箱中执行 Python 代码"""
    code = arguments.get("code", "")
    session_id = arguments.get("session_id", "mcp-default")
    timeout = arguments.get("timeout", 60)

    if not code:
        return [TextContent(type="text", text=json.dumps({"success": False, "error": "code is required"}, ensure_ascii=False))]

    try:
        from anyrun import Sandbox

        sandbox = Sandbox()

        def _run():
            return sandbox.run(code, session_id=session_id, timeout=timeout)

        result = await asyncio.to_thread(_run)

        return [TextContent(
            type="text",
            text=json.dumps({
                "success": result.success,
                "data": result.data,
                "error": result.error,
                "metadata": result.metadata,
            }, ensure_ascii=False),
        )]
    except Exception as e:
        return [TextContent(type="text", text=json.dumps({"success": False, "error": str(e)}, ensure_ascii=False))]


async def handle_trace_list(arguments: dict) -> list[TextContent]:
    """列出执行轨迹"""
    limit = arguments.get("limit", 20)
    error_only = arguments.get("error_only", False)

    try:
        from anyrun.tracing.collector import get_store

        store = get_store()
        traces = await asyncio.to_thread(store.list, error_only=error_only, limit=limit)
        return [TextContent(type="text", text=json.dumps(traces, ensure_ascii=False, indent=2))]
    except Exception as e:
        return [TextContent(type="text", text=json.dumps({"error": str(e)}, ensure_ascii=False))]


async def handle_trace_get(arguments: dict) -> list[TextContent]:
    """获取单条轨迹"""
    trace_id = arguments.get("trace_id", "")
    if not trace_id:
        return [TextContent(type="text", text=json.dumps({"error": "trace_id required"}, ensure_ascii=False))]

    try:
        from anyrun.tracing.collector import get_store

        store = get_store()

        def _get():
            return store.get(trace_id)

        trace = await asyncio.to_thread(_get)
        if trace is None:
            return [TextContent(type="text", text=json.dumps({"error": "not found"}, ensure_ascii=False))]
        return [TextContent(type="text", text=json.dumps(trace.to_dict(), ensure_ascii=False, indent=2))]
    except Exception as e:
        return [TextContent(type="text", text=json.dumps({"error": str(e)}, ensure_ascii=False))]


async def handle_trace_stats(arguments: dict) -> list[TextContent]:
    """获取统计信息"""
    try:
        from anyrun.tracing.collector import get_store

        store = get_store()

        def _stats():
            return store.stats()

        stats = await asyncio.to_thread(_stats)
        return [TextContent(type="text", text=json.dumps(stats, ensure_ascii=False, indent=2))]
    except Exception as e:
        return [TextContent(type="text", text=json.dumps({"error": str(e)}, ensure_ascii=False))]


# MCP 工具定义
TOOLS = [
    Tool(
        name="sandbox_run",
        description="在 Docker 沙箱中安全执行 Python 代码。传入任意 Python 代码字符串，在隔离容器中运行并返回 stdout 输出。",
        inputSchema={
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "要执行的 Python 代码",
                },
                "session_id": {
                    "type": "string",
                    "description": "会话 ID，同一会话共享容器和文件系统",
                    "default": "mcp-default",
                },
                "timeout": {
                    "type": "integer",
                    "description": "超时秒数",
                    "default": 60,
                },
            },
            "required": ["code"],
        },
    ),
    Tool(
        name="trace_list",
        description="列出 anyrun 的执行轨迹。可用于排查问题、分析失败模式。",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "返回条数",
                    "default": 20,
                },
                "error_only": {
                    "type": "boolean",
                    "description": "只看失败的",
                    "default": False,
                },
            },
        },
    ),
    Tool(
        name="trace_get",
        description="获取单条执行轨迹的详细信息，包括代码、输出、错误和堆栈。",
        inputSchema={
            "type": "object",
            "properties": {
                "trace_id": {
                    "type": "string",
                    "description": "轨迹 ID",
                },
            },
            "required": ["trace_id"],
        },
    ),
    Tool(
        name="trace_stats",
        description="获取 anyrun 的执行统计：总量、成功率、常见错误等。",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
]

HANDLERS = {
    "sandbox_run": handle_sandbox_run,
    "trace_list": handle_trace_list,
    "trace_get": handle_trace_get,
    "trace_stats": handle_trace_stats,
}


async def main():
    _discover_docker_host()

    server = Server("anyrun")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        handler = HANDLERS.get(name)
        if handler is None:
            return [TextContent(type="text", text=json.dumps({"error": f"unknown tool: {name}"}, ensure_ascii=False))]
        return await handler(arguments)

    async with stdio_server() as (read, write):
        await server.run(
            read,
            write,
            InitializationOptions(
                server_name="anyrun",
                server_version="1.0.2",
                capabilities=ServerCapabilities(tools=ToolsCapability(listChanged=False)),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
