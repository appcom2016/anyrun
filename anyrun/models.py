"""统一数据模型 — 所有数据类集中定义"""

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


# ── 工具 & 技能模型 ──────────────────────────────────────────


@dataclass
class Tool:
    """工具定义"""
    name: str
    description: str
    parameters: dict  # JSON Schema 风格的参数定义
    code: str         # 工具执行代码（Python 函数定义）
    status: str = "beta"
    version: int = 1

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Skill:
    """技能元数据（从 SKILL.md 的 YAML frontmatter 解析）"""
    name: str
    description: str
    path: str

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description, "path": self.path}


# ── 执行相关模型 ────────────────────────────────────────────


class ContainerStatus(str, Enum):
    """容器状态"""
    RUNNING = "running"
    STOPPED = "stopped"
    PAUSED = "paused"
    EXITED = "exited"
    NOT_FOUND = "not_found"


@dataclass
class ContainerInfo:
    """容器信息"""
    id: str
    name: str
    status: ContainerStatus
    image: str
    created_at: str
    session_id: str
    ports: dict = field(default_factory=dict)


@dataclass
class ExecutionConfig:
    """执行安全配置"""
    timeout: int = 60
    memory_limit: Optional[str] = "512m"
    cpu_shares: Optional[int] = 1024
    network_disabled: bool = False
    read_only_rootfs: bool = False
    user: Optional[str] = None
    container_port: int = 8080
    host_port: Optional[int] = None


@dataclass
class ToolExecutionRequest:
    """工具执行请求"""
    tool_code: str
    parameters: dict
    session_id: str
    tool_name: str
    config: Optional[ExecutionConfig] = None


@dataclass
class ExecutionResult:
    """执行结果（统一响应格式）"""
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    logs: Optional[dict] = None
    execution_time: Optional[float] = None
    metadata: Optional[dict] = None

    @classmethod
    def ok(cls, data: Any, metadata: Optional[dict] = None) -> "ExecutionResult":
        return cls(success=True, data=data, metadata=metadata)

    @classmethod
    def fail(cls, error: str, logs: Optional[dict] = None) -> "ExecutionResult":
        return cls(success=False, error=error, logs=logs)
