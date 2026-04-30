"""路径映射 — 宿主机路径 ↔ 容器路径双向转换"""

import os
from typing import Tuple


class PathMapper:
    """负责宿主机工作区路径与容器内部路径之间的转换。

    所有工具执行都在容器内进行，但文件存储在宿主机上，
    PathMapper 确保两边路径正确对应。
    """

    def __init__(
        self,
        host_workspace_root: str,
        container_workspace_root: str = "/app/workspace",
    ):
        self.host_root = os.path.abspath(host_workspace_root)
        self.container_root = container_workspace_root

    def host_to_container(self, host_path: str) -> str:
        """宿主机绝对路径 → 容器内路径"""
        host_path = os.path.abspath(host_path)
        if not host_path.startswith(self.host_root):
            raise ValueError(
                f"路径 {host_path} 不在工作区根目录 {self.host_root} 内"
            )
        relative = os.path.relpath(host_path, self.host_root)
        return os.path.join(self.container_root, relative)

    def container_to_host(self, container_path: str) -> str:
        """容器内路径 → 宿主机绝对路径"""
        if not container_path.startswith(self.container_root):
            raise ValueError(
                f"路径 {container_path} 不在容器工作区 {self.container_root} 内"
            )
        relative = os.path.relpath(container_path, self.container_root)
        return os.path.join(self.host_root, relative)

    def get_session_paths(self, session_id: str) -> Tuple[str, str]:
        """获取某会话的宿主机目录和容器目录

        宿主机: {host_root}/{session_id}/
        容器:   {container_root}/
        """
        host_session_dir = os.path.join(self.host_root, session_id)
        os.makedirs(host_session_dir, exist_ok=True)
        return host_session_dir, self.container_root
