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

import json
import sys
import os


def ensure_docker_host():
    """确保 macOS 上能找到 Docker socket"""
    if "DOCKER_HOST" in os.environ:
        return
    mac_sock = os.path.expanduser("~/.docker/run/docker.sock")
    if os.path.exists(mac_sock):
        os.environ["DOCKER_HOST"] = f"unix://{mac_sock}"


def handle_sandbox_run(arguments: dict) -> list:
    """执行 sandbox_run 工具"""
    code = arguments.get("code", "")
    session_id = arguments.get("session_id", "mcp-default")
    timeout = arguments.get("timeout", 60)

    if not code:
        return [{"type": "text", "text": json.dumps({"success": False, "error": "code is required"})}]

    try:
        from anyrun import Sandbox
        sandbox = Sandbox()
        result = sandbox.run(code, session_id=session_id, timeout=timeout)

        return [{"type": "text", "text": json.dumps({
            "success": result.success,
            "data": result.data,
            "error": result.error,
            "metadata": result.metadata,
        }, ensure_ascii=False)}]
    except Exception as e:
        return [{"type": "text", "text": json.dumps({"success": False, "error": str(e)})}]


def handle_trace_list(arguments: dict) -> list:
    """列出执行轨迹"""
    limit = arguments.get("limit", 20)
    error_only = arguments.get("error_only", False)

    try:
        from anyrun.tracing.collector import get_store
        store = get_store()
        traces = store.list(error_only=error_only, limit=limit)
        return [{"type": "text", "text": json.dumps(traces, ensure_ascii=False, indent=2)}]
    except Exception as e:
        return [{"type": "text", "text": json.dumps({"error": str(e)})}]


def handle_trace_get(arguments: dict) -> list:
    """获取单条轨迹"""
    trace_id = arguments.get("trace_id", "")
    if not trace_id:
        return [{"type": "text", "text": json.dumps({"error": "trace_id required"})}]

    try:
        from anyrun.tracing.collector import get_store
        store = get_store()
        trace = store.get(trace_id)
        if trace is None:
            return [{"type": "text", "text": json.dumps({"error": "not found"})}]
        return [{"type": "text", "text": json.dumps(trace.to_dict(), ensure_ascii=False, indent=2)}]
    except Exception as e:
        return [{"type": "text", "text": json.dumps({"error": str(e)})}]


def handle_trace_stats(arguments: dict) -> list:
    """获取统计信息"""
    try:
        from anyrun.tracing.collector import get_store
        store = get_store()
        stats = store.stats()
        return [{"type": "text", "text": json.dumps(stats, ensure_ascii=False, indent=2)}]
    except Exception as e:
        return [{"type": "text", "text": json.dumps({"error": str(e)})}]


# MCP 工具定义
TOOLS = [
    {
        "name": "sandbox_run",
        "description": "在 Docker 沙箱中安全执行 Python 代码。传入任意 Python 代码字符串，在隔离容器中运行并返回 stdout 输出。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "要执行的 Python 代码"
                },
                "session_id": {
                    "type": "string",
                    "description": "会话 ID，同一会话共享容器和文件系统",
                    "default": "mcp-default"
                },
                "timeout": {
                    "type": "integer",
                    "description": "超时秒数",
                    "default": 60
                },
            },
            "required": ["code"],
        },
    },
    {
        "name": "trace_list",
        "description": "列出 anyrun 的执行轨迹。可用于排查问题、分析失败模式。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "返回条数",
                    "default": 20
                },
                "error_only": {
                    "type": "boolean",
                    "description": "只看失败的",
                    "default": False
                },
            },
        },
    },
    {
        "name": "trace_get",
        "description": "获取单条执行轨迹的详细信息，包括代码、输出、错误和堆栈。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trace_id": {
                    "type": "string",
                    "description": "轨迹 ID"
                },
            },
            "required": ["trace_id"],
        },
    },
    {
        "name": "trace_stats",
        "description": "获取 anyrun 的执行统计：总量、成功率、常见错误等。",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]

HANDLERS = {
    "sandbox_run": handle_sandbox_run,
    "trace_list": handle_trace_list,
    "trace_get": handle_trace_get,
    "trace_stats": handle_trace_stats,
}


def main():
    ensure_docker_host()

    # 使用 mcp SDK
    from mcp.server import Server
    from mcp.server.stdio import stdio_server

    server = Server("anyrun")

    @server.list_tools()
    async def list_tools():
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        handler = HANDLERS.get(name)
        if handler is None:
            return [{"type": "text", "text": json.dumps({"error": f"unknown tool: {name}"})}]
        return handler(arguments)

    import asyncio

    async def run():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(run())


if __name__ == "__main__":
    main()
