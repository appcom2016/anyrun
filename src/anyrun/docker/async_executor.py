"""异步 Docker 工具执行器 — asyncio 封装，供 async Agent 使用"""

import asyncio
from typing import Optional

from ..models import ToolExecutionRequest, ExecutionResult
from .executor import DockerToolExecutor


class AsyncDockerToolExecutor:
    """异步 Docker 工具执行器

    将同步的 DockerToolExecutor 包装为 async 接口，
    适合集成到 asyncio 风格的 Agent 中。

    需要额外安装: pip install aiodocker
    """

    def __init__(self, host_workspace_root: str, docker_image: str = "python:3.12-slim"):
        self._sync = DockerToolExecutor(host_workspace_root, docker_image)
        self._docker = None

    async def initialize(self):
        """异步初始化（验证 Docker 连接）"""
        import aiodocker
        self._docker = aiodocker.Docker()
        await self._docker._query_json("_ping", "GET")

    async def execute_tool(self, request: ToolExecutionRequest) -> ExecutionResult:
        """异步执行工具（线程池中执行同步代码）"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._sync.execute_tool, request)

    async def cleanup_session(self, session_id: str) -> bool:
        """异步清理会话"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._sync.cleanup_session, session_id
        )

    async def cleanup(self):
        """清理资源"""
        if self._docker:
            await self._docker.close()
            self._docker = None
