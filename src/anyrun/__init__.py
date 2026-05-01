"""
anyrun — AI Agent 的 Docker 沙箱执行引擎。

为 Agent 提供安全、隔离的工具执行环境，
自动采集全量执行轨迹，为经验提取和自进化提供数据基础。

核心组件：
- Sandbox        — 一行代码在 Docker 中安全执行代码
- ToolRegistry   — 工具和技能的管理与持久化

简单用法：
    from anyrun import Sandbox, ToolRegistry

    sandbox = Sandbox(image="python:3.12-slim")
    result = sandbox.run(code='print("hello")')
    # -> {"success": True, "result": "hello\n", "logs": {...}}

    registry = ToolRegistry()
    registry.add_tool(name="shell", code="...", parameters={...})
    tools = registry.get_tools_for_llm()  # -> OpenAI tool format
"""

from .config import SystemConfig
from .models import (
    ContainerInfo,
    ContainerStatus,
    ExecutionConfig,
    ExecutionResult,
    Skill,
    Tool,
    ToolExecutionRequest,
)
from .toolbox import Toolbox as ToolRegistry
from .docker import (
    DockerToolExecutor as Sandbox,
    AsyncDockerToolExecutor as AsyncSandbox,
    ContainerManager,
    PathMapper,
)

__version__ = "1.3.0"

__all__ = [
    # 配置
    "SystemConfig",
    # 模型
    "Tool",
    "Skill",
    "ToolExecutionRequest",
    "ExecutionResult",
    "ExecutionConfig",
    "ContainerInfo",
    "ContainerStatus",
    # 核心
    "ToolRegistry",
    "Sandbox",
    "AsyncSandbox",
    # 高级
    "ContainerManager",
    "PathMapper",
]
