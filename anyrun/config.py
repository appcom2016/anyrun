"""系统配置 — 所有可配置参数集中管理"""

from dataclasses import dataclass, field


@dataclass
class SystemConfig:
    """anyrun 全局配置

    可通过环境变量或直接赋值覆盖默认值。
    """

    # 工作区路径
    host_workspace_root: str = "./workspace"
    container_workspace_root: str = "/app/workspace"

    # Docker 镜像
    docker_image: str = "python:3.12-slim"

    # 工具存储
    tool_storage_path: str = "./data/toolbox.json"

    # Skills 目录
    skills_dir: str = "./skills"

    # 执行默认值
    default_timeout: int = 60
    memory_limit: str = "512m"
    cpu_shares: int = 1024
    network_disabled: bool = False
    read_only_rootfs: bool = False
