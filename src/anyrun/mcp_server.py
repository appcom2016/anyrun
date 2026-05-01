"""anyrun MCP Server — 动态暴露 Toolbox 工具 + 内置管理工具

让 Hermes / Claude Desktop 等 MCP 客户端能调用 anyrun 的所有工具，
并且 Agent 可以通过 MCP 自我迭代工具库。

启动方式:
    python -m anyrun.mcp_server

Hermes 配置 (config.yaml):
    mcp_servers:
      anyrun:
        command: "/usr/local/bin/python3"
        args: ["-m", "anyrun.mcp_server"]

暴露的工具分类:
  1. 代码执行
     - sandbox_run: 沙箱执行任意 Python 代码
  2. 轨迹管理
     - trace_list / trace_get / trace_stats: 执行轨迹查询
  3. Toolbox 管理（工具的生命周期管理）
     - toolbox_add_tool / toolbox_get_tool / toolbox_update_tool_code
     - toolbox_promote_tool / toolbox_delete_tool
     - toolbox_get_tools_info / toolbox_get_tool_count
     - toolbox_get_skill / toolbox_get_skills_info / toolbox_get_skills_prompt
  4. Toolbox 用户工具（动态注册）
     - shell, create_file 等通过 ToolRegistry.add_tool() 添加的工具
"""

import asyncio
import json
import sys
import os
from typing import Any, Optional

from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import Tool as McpTool, TextContent, ServerCapabilities, ToolsCapability

# ── 注意：anyrun.models / anyrun.toolbox / anyrun.Sandbox
#    在模块顶部不导入，因为 anyrun/__init__.py 会触发 import docker。
#    所有 anyrun 相关导入都在 handler 函数内延迟加载。 ──

# ── 延迟初始化缓存 ───────────────────────────
_toolbox: Any = None
_sandbox: Any = None


def _get_toolbox():
    global _toolbox
    if _toolbox is None:
        from .toolbox import Toolbox
        _toolbox = Toolbox()
    return _toolbox


def _get_sandbox():
    global _sandbox
    if _sandbox is None:
        from anyrun import Sandbox
        _sandbox = Sandbox()
    return _sandbox


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


# ── 参数格式转换 ─────────────────────────────────────


def _tool_params_to_schema(params: dict) -> dict:
    """将 Toolbox 参数定义（含 required/default/enum）转为 MCP inputSchema"""
    properties = {}
    required = []
    for name, spec in params.items():
        prop = {}
        for key in ("type", "description", "default", "enum"):
            if key in spec:
                prop[key] = spec[key]
        properties[name] = prop
        if spec.get("required"):
            required.append(name)
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _text_ok(data: Any) -> list[TextContent]:
    """将任意可 JSON 序列化的数据包装为 MCP TextContent 响应"""
    return [TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2))]


def _text_error(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps({"error": msg}, ensure_ascii=False))]


# ══════════════════════════════════════════════════════════════
#                    代码执行 Handler
# ══════════════════════════════════════════════════════════════


async def handle_sandbox_run(arguments: dict) -> list[TextContent]:
    """在 Docker 沙箱中执行 Python 代码"""
    code = arguments.get("code", "")
    session_id = arguments.get("session_id", "mcp-default")
    timeout = arguments.get("timeout", 60)

    if not code:
        return _text_error("code is required")

    try:
        sandbox = _get_sandbox()

        def _run():
            return sandbox.run(code, session_id=session_id, timeout=timeout)

        result = await asyncio.wait_for(asyncio.to_thread(_run), timeout=timeout + 5)

        return _text_ok({
            "success": result.success,
            "data": result.data,
            "error": result.error,
            "metadata": result.metadata,
        })
    except Exception as e:
        return _text_ok({"success": False, "error": str(e)})


# ══════════════════════════════════════════════════════════════
#                    轨迹管理 Handler
# ══════════════════════════════════════════════════════════════


async def handle_trace_list(arguments: dict) -> list[TextContent]:
    """列出执行轨迹"""
    limit = arguments.get("limit", 20)
    error_only = arguments.get("error_only", False)

    try:
        from tracing.collector import get_store
        store = get_store()
        traces = await asyncio.to_thread(store.list, error_only=error_only, limit=limit)
        return [TextContent(type="text", text=json.dumps(traces, ensure_ascii=False, indent=2))]
    except Exception as e:
        return _text_error(str(e))


async def handle_trace_get(arguments: dict) -> list[TextContent]:
    """获取单条轨迹"""
    trace_id = arguments.get("trace_id", "")
    if not trace_id:
        return _text_error("trace_id required")

    try:
        from tracing.collector import get_store
        store = get_store()

        def _get():
            return store.get(trace_id)

        trace = await asyncio.to_thread(_get)
        if trace is None:
            return _text_error("not found")
        return [TextContent(type="text", text=json.dumps(trace.to_dict(), ensure_ascii=False, indent=2))]
    except Exception as e:
        return _text_error(str(e))


async def handle_trace_stats(arguments: dict) -> list[TextContent]:
    """获取统计信息"""
    try:
        from tracing.collector import get_store
        store = get_store()

        def _stats():
            return store.stats()

        stats = await asyncio.to_thread(_stats)
        return [TextContent(type="text", text=json.dumps(stats, ensure_ascii=False, indent=2))]
    except Exception as e:
        return _text_error(str(e))


# ══════════════════════════════════════════════════════════════
#                 Session 管理 Handler
# ══════════════════════════════════════════════════════════════


async def handle_session_list(arguments: dict) -> list[TextContent]:
    """列出所有活跃的沙箱会话及其容器拓扑信息"""
    sandbox = _get_sandbox()
    sessions = {}
    try:
        from docker.container import ContainerManager
        mgr = ContainerManager(logger=_get_sandbox().logger)
        for container in mgr.client.containers.list(
            filters={"label": "managed_by=container_manager"}
        ):
            sid = container.labels.get("session_id", "unknown")
            tags = container.image.tags
            sessions[sid] = {
                "container_id": container.id[:12],
                "status": container.status,
                "image": tags[0] if tags else "unknown",
                "ports": container.ports or {},
                "created": container.attrs.get("Created", ""),
                "name": container.name,
            }
    except Exception as e:
        return _text_error(f"查询会话失败: {e}")

    return _text_ok({"sessions": sessions, "total": len(sessions)})


async def handle_session_cleanup(arguments: dict) -> list[TextContent]:
    """清理指定或所有沙箱会话容器"""
    sandbox = _get_sandbox()
    session_id = arguments.get("session_id", "")
    delete = arguments.get("delete", False)

    if session_id:
        ok = sandbox.cleanup_session(session_id, delete=delete)
        return _text_ok({"session_id": session_id, "cleaned": ok})
    else:
        from docker.container import ContainerManager
        mgr = ContainerManager(logger=_get_sandbox().logger)
        cleaned = []
        for container in mgr.client.containers.list(
            filters={"label": "managed_by=container_manager"}
        ):
            sid = container.labels.get("session_id", "")
            if sid:
                sandbox.cleanup_session(sid, delete=delete)
                cleaned.append(sid)
        return _text_ok({"cleaned_sessions": cleaned, "count": len(cleaned)})


# ══════════════════════════════════════════════════════════════
#                  Toolbox 管理 Handler
# ══════════════════════════════════════════════════════════════


async def handle_toolbox_add_tool(arguments: dict) -> list[TextContent]:
    """向 Toolbox 添加一个新工具"""
    toolbox = _get_toolbox()
    from .models import Tool as AnyTool

    name = arguments.get("name", "").strip()
    if not name:
        return _text_error("name is required")

    description = arguments.get("description", "")
    code = arguments.get("code", "")
    if not code:
        return _text_error("code is required")

    # parameters 可以是 JSON 字符串或 dict
    params_raw = arguments.get("parameters", "{}")
    if isinstance(params_raw, str):
        try:
            parameters = json.loads(params_raw) if params_raw.strip() else {}
        except json.JSONDecodeError as e:
            return _text_error(f"parameters 不是有效的 JSON: {e}")
    else:
        parameters = params_raw

    tool = AnyTool(name=name, description=description, parameters=parameters, code=code)
    toolbox.add_tool(tool)
    return _text_ok({"success": True, "name": name})


async def handle_toolbox_get_tool(arguments: dict) -> list[TextContent]:
    """获取单个工具的详情"""
    toolbox = _get_toolbox()
    name = arguments.get("name", "")
    tool = toolbox.get_tool(name)
    if tool is None:
        return _text_error(f"tool '{name}' not found")
    return _text_ok(tool.to_dict())


async def handle_toolbox_update_tool_code(arguments: dict) -> list[TextContent]:
    """更新一个工具的代码（自动递增版本号）"""
    toolbox = _get_toolbox()
    name = arguments.get("name", "")
    new_code = arguments.get("code", "")
    if not name:
        return _text_error("name is required")
    if not new_code:
        return _text_error("code is required")
    tool = toolbox.update_tool_code(name, new_code)
    if tool is None:
        return _text_error(f"tool '{name}' not found")
    return _text_ok({"success": True, "name": name, "version": tool.version})


async def handle_toolbox_promote_tool(arguments: dict) -> list[TextContent]:
    """将工具从 beta 提升为 prod"""
    toolbox = _get_toolbox()
    name = arguments.get("name", "")
    if toolbox.promote_tool(name):
        return _text_ok({"success": True, "name": name, "status": "prod"})
    return _text_error(f"tool '{name}' not found")


async def handle_toolbox_delete_tool(arguments: dict) -> list[TextContent]:
    """从 Toolbox 删除一个工具"""
    toolbox = _get_toolbox()
    name = arguments.get("name", "")
    if toolbox.delete_tool(name):
        return _text_ok({"success": True, "name": name})
    return _text_error(f"tool '{name}' not found")


async def handle_toolbox_get_tools_info(arguments: dict) -> list[TextContent]:
    """获取所有工具的摘要信息"""
    toolbox = _get_toolbox()
    info = toolbox.get_tools_info()
    return _text_ok(info)


async def handle_toolbox_get_tool_count(arguments: dict) -> list[TextContent]:
    """获取工具总数"""
    toolbox = _get_toolbox()
    count = toolbox.get_tool_count()
    return _text_ok({"count": count})


async def handle_toolbox_get_skill(arguments: dict) -> list[TextContent]:
    """获取单个技能元数据"""
    toolbox = _get_toolbox()
    name = arguments.get("name", "")
    skill = toolbox.get_skill(name)
    if skill is None:
        return _text_error(f"skill '{name}' not found")
    return _text_ok(skill.to_dict())


async def handle_toolbox_get_skills_info(arguments: dict) -> list[TextContent]:
    """获取所有技能的摘要列表"""
    toolbox = _get_toolbox()
    info = toolbox.get_skills_info()
    return _text_ok(info)


async def handle_toolbox_get_skills_prompt(arguments: dict) -> list[TextContent]:
    """以 LLM 友好的格式返回所有技能信息"""
    toolbox = _get_toolbox()
    prompt = toolbox.get_skills_prompt()
    return [TextContent(type="text", text=prompt)]


# ══════════════════════════════════════════════════════════════
#                  Toolbox 用户工具执行 Handler
# ══════════════════════════════════════════════════════════════


async def handle_toolbox_tool(tool_name: str, arguments: dict) -> list[TextContent]:
    """通过 Sandbox 执行 Toolbox 中的注册工具。

    注意：_session_id 和 _timeout 是 MCP 层的元参数，不会传给工具代码。
    默认 session_id 与 sandbox_run 一致（mcp-default），
    确保 create_file / shell / sandbox_run 共享同一容器和文件系统。
    """
    toolbox = _get_toolbox()
    tool = toolbox.get_tool(tool_name)
    if tool is None:
        return _text_error(f"tool '{tool_name}' not found in Toolbox")

    try:
        from .models import ToolExecutionRequest, ExecutionConfig

        # 剥离元参数，防止污染工具代码的 execute_tool(**params)
        tool_params = {k: v for k, v in arguments.items() if not k.startswith("_")}
        # 统一为 mcp-default，与 sandbox_run 默认 session 一致
        session_id = arguments.get("_session_id", "mcp-default")
        timeout = arguments.get("_timeout", 60)

        sandbox = _get_sandbox()
        request = ToolExecutionRequest(
            tool_code=tool.code,
            parameters=tool_params,
            session_id=session_id,
            tool_name=tool.name,
            config=ExecutionConfig(timeout=timeout),
        )

        def _exec():
            return sandbox.execute_tool(request)

        result = await asyncio.wait_for(
            asyncio.to_thread(_exec),
            timeout=(request.config or ExecutionConfig()).timeout + 5,
        )

        return _text_ok({
            "success": result.success,
            "data": result.data,
            "error": result.error,
            "metadata": result.metadata,
        })
    except Exception as e:
        return _text_ok({"success": False, "error": str(e)})


# ══════════════════════════════════════════════════════════════
#                     工具定义
# ══════════════════════════════════════════════════════════════

TOOLBOX_MGMT_TOOLS = [
    McpTool(
        name="toolbox_add_tool",
        description="向 Toolbox 注册一个新工具。Agent 可将常用能力抽象为可复用工具。name 必填，parameters 是 JSON Schema 格式的 JSON 字符串（每个参数可含 type/description/default/enum/required 字段），code 是包含 execute_tool 函数的 Python 代码。",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "工具名称（唯一标识）"},
                "description": {"type": "string", "description": "工具描述"},
                "parameters": {"type": "string", "description": "参数 JSON Schema 的 JSON 字符串，如 {\"input\": {\"type\": \"string\", \"description\": \"输入\", \"required\": true}}"},
                "code": {"type": "string", "description": "Python 代码，必须包含 def execute_tool(**params) 函数"},
            },
            "required": ["name", "code"],
        },
    ),
    McpTool(
        name="toolbox_get_tool",
        description="获取单个工具的完整详情，包括 name, description, parameters, code, status, version。",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "工具名称"},
            },
            "required": ["name"],
        },
    ),
    McpTool(
        name="toolbox_update_tool_code",
        description="更新一个工具的代码，自动递增版本号并重置为 beta 状态。适合在 Agent 发现工具 bug 后修复。",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "工具名称"},
                "code": {"type": "string", "description": "新 Python 代码"},
            },
            "required": ["name", "code"],
        },
    ),
    McpTool(
        name="toolbox_promote_tool",
        description="将工具从 beta 提升为 prod。代表该工具经过验证、稳定可用。",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "工具名称"},
            },
            "required": ["name"],
        },
    ),
    McpTool(
        name="toolbox_delete_tool",
        description="从 Toolbox 中删除一个工具。谨慎操作。",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "工具名称"},
            },
            "required": ["name"],
        },
    ),
    McpTool(
        name="toolbox_get_tools_info",
        description="获取所有注册工具的摘要列表（含 name, description, parameters, status, version），适合让 LLM 了解当前可用工具集。",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    McpTool(
        name="toolbox_get_tool_count",
        description="获取 Toolbox 中的工具总数。",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    McpTool(
        name="toolbox_get_skill",
        description="获取单个技能（Skill）的元数据，包括 name, description, path。",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "技能名称"},
            },
            "required": ["name"],
        },
    ),
    McpTool(
        name="toolbox_get_skills_info",
        description="获取所有已加载技能（Skill）的摘要列表。",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    McpTool(
        name="toolbox_get_skills_prompt",
        description="以 LLM 友好的纯文本格式返回所有技能信息，可直接嵌入提示词。",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
]

TOOLBOX_MGMT_HANDLERS = {
    "toolbox_add_tool": handle_toolbox_add_tool,
    "toolbox_get_tool": handle_toolbox_get_tool,
    "toolbox_update_tool_code": handle_toolbox_update_tool_code,
    "toolbox_promote_tool": handle_toolbox_promote_tool,
    "toolbox_delete_tool": handle_toolbox_delete_tool,
    "toolbox_get_tools_info": handle_toolbox_get_tools_info,
    "toolbox_get_tool_count": handle_toolbox_get_tool_count,
    "toolbox_get_skill": handle_toolbox_get_skill,
    "toolbox_get_skills_info": handle_toolbox_get_skills_info,
    "toolbox_get_skills_prompt": handle_toolbox_get_skills_prompt,
}

SANDBOX_TOOLS = [
    McpTool(
        name="sandbox_run",
        description="在 Docker 沙箱中安全执行任意 Python 代码。传入代码字符串，返回 stdout 输出与执行元数据。",
        inputSchema={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "要执行的 Python 代码"},
                "session_id": {"type": "string", "description": "会话 ID，同一会话共享容器和文件系统", "default": "mcp-default"},
                "timeout": {"type": "integer", "description": "超时秒数", "default": 60},
            },
            "required": ["code"],
        },
    ),
]

SANDBOX_HANDLERS = {
    "sandbox_run": handle_sandbox_run,
}

TRACE_TOOLS = [
    McpTool(
        name="trace_list",
        description="列出 anyrun 的执行轨迹。可用于排查问题、分析失败模式。",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "返回条数", "default": 20},
                "error_only": {"type": "boolean", "description": "只看失败的", "default": False},
            },
        },
    ),
    McpTool(
        name="trace_get",
        description="获取单条执行轨迹的详细信息，包括代码、输出、错误和堆栈。",
        inputSchema={
            "type": "object",
            "properties": {
                "trace_id": {"type": "string", "description": "轨迹 ID"},
            },
            "required": ["trace_id"],
        },
    ),
    McpTool(
        name="trace_stats",
        description="获取 anyrun 的执行统计：总量、成功率、常见错误等。",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
]

TRACE_HANDLERS = {
    "trace_list": handle_trace_list,
    "trace_get": handle_trace_get,
    "trace_stats": handle_trace_stats,
}

SESSION_TOOLS = [
    McpTool(
        name="session_list",
        description="列出所有活跃的 Docker 沙箱会话及其拓扑信息。每个 session 对应一个独立的容器，显示容器 ID、状态、镜像和端口映射。",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    McpTool(
        name="session_cleanup",
        description="清理 sandbox 会话容器。指定 session_id 清理单个；不指定则清理所有。 session_id='all' 或空字符串清理所有。 delete=true 时同时删除容器镜像。",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "会话 ID，留空清理所有"},
                "delete": {"type": "boolean", "description": "是否删除容器", "default": False},
            },
        },
    ),
]

SESSION_HANDLERS = {
    "session_list": handle_session_list,
    "session_cleanup": handle_session_cleanup,
}

# 合并所有内置工具
BUILTIN_TOOLS = SANDBOX_TOOLS + TRACE_TOOLS + TOOLBOX_MGMT_TOOLS + SESSION_TOOLS
BUILTIN_HANDLERS = {}
BUILTIN_HANDLERS.update(SANDBOX_HANDLERS)
BUILTIN_HANDLERS.update(TRACE_HANDLERS)
BUILTIN_HANDLERS.update(TOOLBOX_MGMT_HANDLERS)
BUILTIN_HANDLERS.update(SESSION_HANDLERS)
BUILTIN_NAMES = {t.name for t in BUILTIN_TOOLS}


# ══════════════════════════════════════════════════════════════
#                      MCP Server
# ══════════════════════════════════════════════════════════════


async def main():
    _discover_docker_host()

    server = Server("anyrun")

    @server.list_tools()
    async def list_tools() -> list[McpTool]:
        """动态返回内置工具 + Toolbox 中的用户工具"""
        tools = list(BUILTIN_TOOLS)

        toolbox = _get_toolbox()
        for info in toolbox.get_tools_info():
            name = info["name"]
            if name in BUILTIN_NAMES:
                continue
            tools.append(McpTool(
                name=name,
                description=info.get("description", ""),
                inputSchema=_tool_params_to_schema(info.get("parameters", {})),
            ))

        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        """按名称分派：内置工具走 BUILTIN_HANDLERS，其余尝试从 Toolbox 查找执行"""
        if name in BUILTIN_HANDLERS:
            return await BUILTIN_HANDLERS[name](arguments)

        toolbox = _get_toolbox()
        tool = toolbox.get_tool(name)
        if tool is not None:
            return await handle_toolbox_tool(name, arguments)

        return _text_error(f"unknown tool: {name}")

    @server.list_resources()
    async def list_resources():
        """列出可用的 MCP 资源：会话列表和执行轨迹"""
        from mcp.types import Resource
        resources = [
            Resource(
                uri="anyrun://sessions",
                name="Active Sessions",
                description="当前活跃的 Docker 沙箱会话拓扑",
                mimeType="application/json",
            ),
            Resource(
                uri="anyrun://traces",
                name="Execution Traces",
                description="最近 100 条工具执行轨迹",
                mimeType="application/json",
            ),
        ]
        try:
            from tracing.collector import get_store
            store = get_store()
            traces = store.list(limit=5)
            for t in traces:
                tid = t.get("trace_id", "")
                if tid:
                    resources.append(Resource(
                        uri=f"anyrun://traces/{tid}",
                        name=f"Trace {tid[:8]}...",
                        description=t.get("error_type", ""),
                        mimeType="application/json",
                    ))
        except Exception:
            pass
        return resources

    @server.read_resource()
    async def read_resource(uri: str):
        """读取指定资源内容"""
        from mcp.types import ResourceContents, TextResourceContents

        if uri == "anyrun://sessions":
            result = await handle_session_list({})
            text = result[0].text if result else "{}"
            return [TextResourceContents(uri=uri, text=text, mimeType="application/json")]

        if uri == "anyrun://traces":
            from tracing.collector import get_store
            store = get_store()
            traces = store.list(limit=100)
            text = json.dumps(traces, ensure_ascii=False, indent=2)
            return [TextResourceContents(uri=uri, text=text, mimeType="application/json")]

        if uri.startswith("anyrun://traces/"):
            trace_id = uri.split("/")[-1]
            from tracing.collector import get_store
            store = get_store()
            trace = store.get(trace_id)
            if trace is None:
                raise ValueError(f"Trace not found: {trace_id}")
            text = json.dumps(trace.to_dict(), ensure_ascii=False, indent=2)
            return [TextResourceContents(uri=uri, text=text, mimeType="application/json")]

        raise ValueError(f"Unknown resource: {uri}")

    @server.list_prompts()
    async def list_prompts():
        """列出可用的 MCP 提示词模板"""
        from mcp.types import Prompt, PromptArgument
        return [
            Prompt(
                name="execute_code",
                description="在 Docker 沙箱中执行 Python 代码并查看结果",
                arguments=[
                    PromptArgument(name="goal", description="执行目标描述", required=True),
                ],
            ),
            Prompt(
                name="add_tool",
                description="向 Toolbox 注册一个新的可复用工具",
                arguments=[
                    PromptArgument(name="tool_name", description="工具名称", required=True),
                    PromptArgument(name="description", description="工具功能描述", required=True),
                ],
            ),
            Prompt(
                name="list_session",
                description="查看当前沙箱会话拓扑（容器状态）",
            ),
        ]

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict | None = None):
        """获取指定提示词的完整内容"""
        from mcp.types import PromptMessage, TextContent as Ptc

        if name == "execute_code":
            goal = (arguments or {}).get("goal", "执行代码")
            return PromptMessage(
                role="user",
                content=Ptc(
                    type="text",
                    text=f"请用 sandbox_run 工具在 Docker 沙箱中执行以下目标的代码：\n\n{goal}\n\n"
                         f"1. 先用 shell 确认环境\n2. 用 create_file 写代码文件\n3. 用 sandbox_run 执行\n4. 用 file_read 查看结果",
                ),
            )

        if name == "add_tool":
            tool_name = (arguments or {}).get("tool_name", "")
            desc = (arguments or {}).get("description", "")
            return PromptMessage(
                role="user",
                content=Ptc(
                    type="text",
                    text=f"请用 toolbox_add_tool 注册一个新工具。\n\n"
                         f"工具名称: {tool_name}\n"
                         f"功能描述: {desc}\n\n"
                         f"注意：代码必须包含 def execute_tool(**params) 函数，"
                         f"返回结果会被 JSON 序列化。",
                ),
            )

        if name == "list_session":
            return PromptMessage(
                role="user",
                content=Ptc(
                    type="text",
                    text="请用 session_list 工具查看当前 Docker 沙箱的会话拓扑，"
                         "列出所有活跃容器及其状态。如果 session_list 返回错误，"
                         "请先检查 Docker 是否运行。",
                ),
            )

        raise ValueError(f"Unknown prompt: {name}")

    async with stdio_server() as (read, write):
        from anyrun import __version__ as anyrun_version
        await server.run(
            read,
            write,
            InitializationOptions(
                server_name="anyrun",
                server_version=anyrun_version,
                capabilities=ServerCapabilities(
                    tools=ToolsCapability(listChanged=False),
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
