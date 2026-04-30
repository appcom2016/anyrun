"""容器管理 — Docker 容器生命周期：创建、确保、执行、清理"""

import logging
import os
import queue
import time
from enum import Enum
from dataclasses import dataclass
from threading import Thread, Event
from typing import Any, Optional, Union, Generator

import docker

from anyrun.models import ContainerInfo, ContainerStatus, ExecutionConfig


# ── 流式消息 ────────────────────────────────────────────────


class MessageType(Enum):
    OUTPUT = "output"
    EXIT = "exit"
    ERROR = "error"
    TIMEOUT = "timeout"


@dataclass
class StreamMessage:
    """流式执行的一条消息"""
    type: MessageType
    data: Optional[dict] = None
    exit_code: Optional[int] = None
    error: Optional[str] = None
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    def to_dict(self) -> dict:
        result: dict = {"type": self.type.value, "timestamp": self.timestamp}
        if self.data:
            result["data"] = self.data
        if self.exit_code is not None:
            result["exit_code"] = self.exit_code
        if self.error:
            result["error"] = self.error
        return result


# ── 容器管理器 ──────────────────────────────────────────────


class ContainerManager:
    """Docker 容器生命周期管理器。

    职责：
    - 按 session_id 创建/获取/销毁容器
    - 在容器内执行命令（阻塞或流式）
    - 确保容器健康（启动等待 + 就绪检查）
    """

    def __init__(
        self,
        docker_client: Optional[Any] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.client = docker_client or self._create_client()
        self.logger = logger or logging.getLogger(__name__)
        self._validate_connection()

    @staticmethod
    def _create_client():
        """创建 Docker 客户端，自动检测 macOS/linux socket 路径"""
        import docker as _docker

        # 先尝试环境变量
        host = os.environ.get("DOCKER_HOST")
        if host:
            return _docker.DockerClient(base_url=host)

        # macOS Docker Desktop 常见 socket 路径
        mac_sockets = [
            os.path.expanduser("~/.docker/run/docker.sock"),
            "/var/run/docker.sock",
        ]
        for sock in mac_sockets:
            if os.path.exists(sock):
                return _docker.DockerClient(base_url=f"unix://{sock}")

        # 最后用默认 from_env
        return _docker.from_env()

    # ── 连接 ───────────────────────────────────────────────

    def _validate_connection(self):
        try:
            self.client.ping()
            self.logger.info("Docker 连接验证成功")
        except Exception as e:
            self.logger.error(f"Docker 连接失败: {e}")
            raise

    # ── 容器生命周期 ───────────────────────────────────────

    def create_container(
        self,
        session_id: str,
        image: str,
        volumes: dict,
        config: Optional[ExecutionConfig] = None,
    ) -> ContainerInfo:
        """创建并启动一个新容器"""
        name = self._container_name(session_id)
        port_bindings = self._build_port_bindings(config)

        container_kwargs: dict = {
            "image": image,
            "command": ["tail", "-f", "/dev/null"],
            "volumes": volumes,
            "working_dir": "/app/workspace",
            "detach": True,
            "name": name,
            "ports": port_bindings,
            "labels": {
                "auto_agent": "sandbox",
                "session_id": session_id,
                "managed_by": "container_manager",
            },
        }

        if config:
            self._apply_security(container_kwargs, config)

        try:
            container = self.client.containers.create(**container_kwargs)
            container.start()
            self._wait_ready(container)
            return self._build_info(container)
        except Exception as e:
            self.logger.error(f"创建容器失败: {e}")
            raise

    def get_container(self, session_id: str) -> Optional[ContainerInfo]:
        """获取容器信息，不存在返回 None"""
        try:
            container = self.client.containers.get(self._container_name(session_id))
            return self._build_info(container)
        except docker.errors.NotFound:
            return None

    def ensure_container(
        self,
        session_id: str,
        image: str,
        volumes: dict,
        config: Optional[ExecutionConfig] = None,
    ) -> ContainerInfo:
        """确保容器存在且运行（幂等）"""
        info = self.get_container(session_id)
        if info is None:
            return self.create_container(session_id, image, volumes, config)

        if info.status != ContainerStatus.RUNNING:
            container = self.client.containers.get(info.id)
            container.start()
            self._wait_ready(container)

        return info

    def cleanup_container(self, session_id: str, delete: bool = False) -> bool:
        """清理容器（停止，可选删除）"""
        try:
            info = self.get_container(session_id)
            if info is None:
                return True
            container = self.client.containers.get(info.id)
            if info.status == ContainerStatus.RUNNING:
                container.stop(timeout=10)
            if delete:
                container.remove(force=True)
            self.logger.info(f"[{session_id}] 容器已清理 (delete={delete})")
            return True
        except Exception as e:
            self.logger.error(f"[{session_id}] 清理容器失败: {e}")
            return False

    # ── 命令执行 ───────────────────────────────────────────

    def execute(
        self,
        session_id: str,
        command: list,
        workdir: str = "/app/workspace",
        timeout: int = 60,
        stream: bool = False,
    ) -> Union[dict, Generator[StreamMessage, None, None]]:
        """在容器中执行命令"""
        info = self.get_container(session_id)
        if info is None:
            raise ValueError(f"会话 {session_id} 的容器不存在")

        container = self.client.containers.get(info.id)
        self.logger.debug(f"[{session_id}] 执行: {' '.join(command)}")

        if stream:
            return self._execute_stream(container, command, workdir, timeout)
        else:
            return self._execute_blocking(container, command, workdir, timeout)

    def execute_with_callback(
        self,
        session_id: str,
        command: list,
        workdir: str = "/app/workspace",
        timeout: int = 60,
        on_output=None,
        on_exit=None,
        on_error=None,
        on_timeout=None,
    ):
        """使用回调的便捷流式执行"""
        for msg in self.execute(
            session_id=session_id,
            command=command,
            workdir=workdir,
            timeout=timeout,
            stream=True,
        ):
            if msg.type == MessageType.OUTPUT and on_output:
                d = msg.data or {}
                on_output(d.get("stdout", ""), d.get("stderr", ""))
            elif msg.type == MessageType.EXIT and on_exit:
                on_exit(msg.exit_code)
            elif msg.type == MessageType.ERROR and on_error:
                on_error(msg.error)
            elif msg.type == MessageType.TIMEOUT and on_timeout:
                on_timeout(msg.error)

    # ── 内部实现 ───────────────────────────────────────────

    def _execute_blocking(
        self, container, command: list, workdir: str, timeout: int
    ) -> dict:
        """阻塞执行，返回完整结果"""
        result = container.exec_run(
            cmd=command,
            workdir=workdir,
            stdout=True,
            stderr=True,
            demux=True,
            environment={"PYTHONUNBUFFERED": "1"},
        )
        stdout, stderr = result.output
        stdout_str = (stdout or b"").decode("utf-8", errors="replace")
        stderr_str = (stderr or b"").decode("utf-8", errors="replace")

        return {
            "exit_code": result.exit_code,
            "stdout": stdout_str,
            "stderr": stderr_str,
            "success": result.exit_code == 0,
        }

    def _execute_stream(
        self, container, command: list, workdir: str, timeout: int
    ) -> Generator[StreamMessage, None, None]:
        """流式执行，逐块产出输出消息"""
        output_queue: queue.Queue = queue.Queue()
        stop_event = Event()

        def _run():
            try:
                exec_id = container.client.api.exec_create(
                    container.id,
                    cmd=command,
                    workdir=workdir,
                    environment={"PYTHONUNBUFFERED": "1"},
                )["Id"]

                stream_data = container.client.api.exec_start(
                    exec_id, stream=True, demux=True
                )

                for stdout_chunk, stderr_chunk in stream_data:
                    if stop_event.is_set():
                        try:
                            container.client.api.exec_kill(exec_id)
                        except Exception:
                            pass
                        break
                    data = {}
                    if stdout_chunk:
                        data["stdout"] = stdout_chunk.decode("utf-8", errors="replace")
                    if stderr_chunk:
                        data["stderr"] = stderr_chunk.decode("utf-8", errors="replace")
                    if data:
                        output_queue.put(StreamMessage(MessageType.OUTPUT, data=data))

                inspect = container.client.api.exec_inspect(exec_id)
                output_queue.put(
                    StreamMessage(MessageType.EXIT, exit_code=inspect["ExitCode"])
                )
            except Exception as e:
                if not stop_event.is_set():
                    output_queue.put(StreamMessage(MessageType.ERROR, error=str(e)))

        thread = Thread(target=_run, daemon=True)
        thread.start()

        start = time.time()
        try:
            while True:
                remaining = timeout - (time.time() - start) if timeout > 0 else 0.1
                if timeout > 0 and remaining <= 0:
                    stop_event.set()
                    yield StreamMessage(
                        MessageType.TIMEOUT,
                        error=f"命令执行超时 (>{timeout}s)",
                    )
                    break

                try:
                    msg = output_queue.get(timeout=min(0.1, max(0.01, remaining)))
                except queue.Empty:
                    if not thread.is_alive():
                        break
                    continue

                if msg.type in (MessageType.EXIT, MessageType.ERROR, MessageType.TIMEOUT):
                    yield msg
                    break
                yield msg
        except Exception as e:
            stop_event.set()
            yield StreamMessage(MessageType.ERROR, error=f"执行监控异常: {e}")
        finally:
            stop_event.set()
            if thread.is_alive():
                thread.join(timeout=2)

    # ── 私有辅助 ───────────────────────────────────────────

    def _container_name(self, session_id: str) -> str:
        return f"auto_agent_sandbox_{session_id}"

    def _build_port_bindings(self, config: Optional[ExecutionConfig]) -> dict:
        if config is None or not config.container_port:
            return {}
        port_key = f"{config.container_port}/tcp"
        return {port_key: config.host_port if config.host_port else None}

    def _apply_security(self, kwargs: dict, config: ExecutionConfig):
        """应用安全限制到容器配置"""
        if config.memory_limit:
            kwargs["mem_limit"] = config.memory_limit
        if config.cpu_shares:
            kwargs["cpu_shares"] = config.cpu_shares
        if config.network_disabled:
            kwargs["network_disabled"] = True
        if config.read_only_rootfs:
            kwargs["read_only"] = True
        if config.user:
            kwargs["user"] = config.user

    def _wait_ready(self, container, timeout: int = 30):
        """等待容器就绪（状态 running + 内部命令可执行）"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                container.reload()
                if container.status == "running":
                    r = container.exec_run(["echo", "ready"])
                    if r.exit_code == 0:
                        return
            except Exception:
                pass
            time.sleep(1)
        raise TimeoutError(f"容器 {container.id[:12]} 启动超时")

    def _build_info(self, container) -> ContainerInfo:
        """从 Docker 容器对象构建 ContainerInfo"""
        tags = container.image.tags
        return ContainerInfo(
            id=container.id[:12],
            name=container.name,
            status=ContainerStatus(container.status),
            image=tags[0] if tags else "unknown",
            created_at=container.attrs.get("Created", ""),
            session_id=container.labels.get("session_id", ""),
            ports=container.ports or {},
        )
