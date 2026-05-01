"""Docker 工具执行器 — 在 Docker 沙箱中安全执行工具代码"""

import hashlib
import json
import logging
import os
import tempfile
import time
from contextlib import contextmanager
from typing import Any, Optional

from ..config import SystemConfig
from ..models import (
    ContainerStatus,
    ExecutionConfig,
    ExecutionResult,
    ToolExecutionRequest,
)
from .container import ContainerManager
from .paths import PathMapper


class DockerToolExecutor:
    """在 Docker 沙箱中执行工具代码。

    工作流程：
    1. 为 session_id 创建/复用沙箱容器
    2. 将工具代码 + 参数写入宿主机临时目录
    3. 生成 harness wrapper 代码
    4. 同步到容器
    5. 在容器内执行 python harness.py
    6. 解析 JSON 结果返回
    """

    def __init__(
        self,
        host_workspace_root: Optional[str] = None,
        docker_image: str = "python:3.12-slim",
        config: Optional[ExecutionConfig] = None,
        logger: Optional[logging.Logger] = None,
    ):
        # 未指定路径时使用稳定目录 ~/.anyrun/workspace/
        # 确保数据在 MCP Server 重启后依然存在
        self._owns_workspace = host_workspace_root is None
        if host_workspace_root is None:
            import pathlib
            stable_dir = pathlib.Path.home() / ".anyrun" / "workspace"
            stable_dir.mkdir(parents=True, exist_ok=True)
            host_workspace_root = str(stable_dir)
        self.host_workspace_root = host_workspace_root

        self.logger = logger or logging.getLogger(__name__)
        self.docker_image = docker_image
        self.config = config or ExecutionConfig()

        self.path_mapper = PathMapper(host_workspace_root)
        self.container_manager = ContainerManager(logger=self.logger)

        self.logger.info(f"DockerToolExecutor 就绪: image={docker_image}")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.cleanup_session("default")
        if self._owns_workspace:
            import shutil
            shutil.rmtree(self.host_workspace_root, ignore_errors=True)
        return False

    def run(self, code: str, session_id: str = "default", timeout: int = 60,
            skill_name: str = "") -> ExecutionResult:
        """在 Docker 沙箱中执行一段 Python 代码。

        Args:
            code: Python 代码字符串
            session_id: 会话标识
            timeout: 超时秒数
            skill_name: 可选，关联的 skill 名称（用于自进化追踪）
        """
        start = time.time()
        sid = session_id

        try:
            # 1. 准备会话路径
            host_dir, cont_dir = self.path_mapper.get_session_paths(sid)

            # 2. 确保容器运行
            volumes = {host_dir: {"bind": cont_dir, "mode": "rw"}}
            cont_info = self.container_manager.ensure_container(
                session_id=sid,
                image=self.docker_image,
                volumes=volumes,
                config=self.config,
            )

            # 3. 准备 run harness
            import tempfile as _tmp
            with _tmp.TemporaryDirectory() as tmp:
                run_hash = hashlib.md5((code + sid).encode()).hexdigest()[:8]
                container_run_dir = f"/app/run_{run_hash}"

                harness_code = _generate_run_harness(code)
                harness_path = os.path.join(tmp, "run.py")
                with open(harness_path, "w", encoding="utf-8") as f:
                    f.write(harness_code)

                self._sync_dir(sid, tmp, container_run_dir)

                # 4. 容器内执行
                exec_result = self.container_manager.execute(
                    session_id=sid,
                    command=["python3", os.path.join(container_run_dir, "run.py")],
                    workdir=container_run_dir,
                    timeout=timeout,
                )

            # 5. 解析结果
            result = self._parse_run_output(exec_result, sid, cont_info, start)

            # 6. 采集执行轨迹
            trace_id = ""
            try:
                from tracing.collector import get_collector
                collector = get_collector()
                trace = collector.collect(
                    session_id=sid,
                    code=code,
                    container_id=cont_info.id if cont_info else "",
                    container_image=self.docker_image,
                    timeout=timeout,
                    success=result.success,
                    result_data=result.data if result.success else None,
                    error_message=result.error if not result.success else None,
                    error_type=result.logs.get("error_type") if result.logs else None,
                    traceback=result.logs.get("traceback") if result.logs else None,
                    start_time=start,
                    end_time=time.time(),
                )
                trace_id = trace.trace_id
            except Exception:
                pass

            # 7. 自进化追踪
            if skill_name:
                try:
                    from ..evolution import record_skill_run
                    record_skill_run(skill_name, result.success, sid, trace_id)
                except Exception:
                    pass

            return result

        except Exception as e:
            self.logger.error(f"[{sid}] run() 异常: {e}", exc_info=True)
            return ExecutionResult.fail(
                error=f"沙箱执行异常: {e}",
                logs={"exception": str(e)},
            )

    # ── 公开接口 ───────────────────────────────────────────

    def execute_tool(self, request: ToolExecutionRequest) -> ExecutionResult:
        """执行工具（主入口）

        Agent 调用此方法执行任意已注册的工具。
        """
        start = time.time()
        sid = request.session_id

        try:
            self.logger.info(f"[{sid}] 执行工具: {request.tool_name}")

            # 1. 准备会话路径
            host_dir, cont_dir = self.path_mapper.get_session_paths(sid)

            # 2. 确保容器运行
            volumes = {host_dir: {"bind": cont_dir, "mode": "rw"}}
            cont_info = self.container_manager.ensure_container(
                session_id=sid,
                image=self.docker_image,
                volumes=volumes,
                config=request.config or self.config,
            )

            # 3. 准备执行文件
            run_info = self._prepare_execution(sid, request)

            # 4. 容器内执行
            exec_result = self.container_manager.execute(
                session_id=sid,
                command=["python3", run_info["harness_path"]],
                workdir=run_info["run_dir"],
                timeout=(request.config or self.config).timeout,
            )

            # 5. 处理结果
            return self._parse_execution_output(exec_result, request, cont_info, start)

        except Exception as e:
            self.logger.error(f"[{sid}] 执行异常: {e}", exc_info=True)
            return ExecutionResult.fail(
                error=f"执行器异常: {e}",
                logs={"exception": str(e)},
            )

    def get_sandbox_info(self, session_id: str) -> Optional[dict]:
        """获取沙箱容器信息"""
        try:
            host_dir, cont_dir = self.path_mapper.get_session_paths(session_id)
            volumes = {host_dir: {"bind": cont_dir, "mode": "rw"}}
            info = self.container_manager.ensure_container(
                session_id=session_id,
                image=self.docker_image,
                volumes=volumes,
                config=self.config,
            )
            return {
                "id": info.id,
                "name": info.name,
                "status": info.status.value,
                "image": info.image,
                "session_id": info.session_id,
                "ports": info.ports,
            }
        except Exception as e:
            self.logger.error(f"[{session_id}] 获取沙箱信息失败: {e}", exc_info=True)
            return None

    def cleanup_session(self, session_id: str, delete: bool = False) -> bool:
        """清理会话容器"""
        return self.container_manager.cleanup_container(session_id, delete=delete)

    def get_session_status(self, session_id: str) -> dict:
        """获取会话状态"""
        try:
            info = self.container_manager.get_container(session_id)
            if info:
                return {
                    "session_id": session_id,
                    "container": {
                        "id": info.id,
                        "name": info.name,
                        "status": info.status.value,
                        "image": info.image,
                        "created_at": info.created_at,
                    },
                    "active": info.status == ContainerStatus.RUNNING,
                }
            return {"session_id": session_id, "container": None, "active": False}
        except Exception as e:
            self.logger.error(f"[{session_id}] 状态查询失败: {e}")
            return {"session_id": session_id, "error": str(e)}

    @contextmanager
    def session_context(self, session_id: str):
        """上下文管理器，确保退出时清理"""
        try:
            yield self
        finally:
            self.cleanup_session(session_id)

    # ── 内部实现 ───────────────────────────────────────────

    def _prepare_execution(self, session_id: str, request: ToolExecutionRequest) -> dict:
        """准备执行文件：写参数 + harness → 同步到容器"""
        with tempfile.TemporaryDirectory() as tmp:
            # 写参数 JSON
            args_path = os.path.join(tmp, "args.json")
            with open(args_path, "w", encoding="utf-8") as f:
                json.dump(request.parameters, f, ensure_ascii=False)

            # 生成 harness
            run_dir_hash = hashlib.md5(
                (request.tool_name + session_id).encode()
            ).hexdigest()[:8]
            container_run_dir = f"/app/run_{run_dir_hash}"
            container_args_path = os.path.join(container_run_dir, "args.json")

            harness_code = _generate_harness(request.tool_code, container_args_path)
            harness_path = os.path.join(tmp, "harness.py")
            with open(harness_path, "w", encoding="utf-8") as f:
                f.write(harness_code)

            # 同步到容器
            self._sync_dir(session_id, tmp, container_run_dir)

            return {
                "args_path": container_args_path,
                "harness_path": os.path.join(container_run_dir, "harness.py"),
                "run_dir": container_run_dir,
            }

    def _sync_dir(self, session_id: str, local_dir: str, container_dir: str):
        """同步本地目录到容器（通过 tar 管道）"""
        import io
        import tarfile

        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            for root, _, files in os.walk(local_dir):
                for fname in files:
                    full_path = os.path.join(root, fname)
                    arcname = os.path.relpath(full_path, start=local_dir)
                    tar.add(full_path, arcname=arcname)

        tar_stream.seek(0)
        info = self.container_manager.get_container(session_id)
        if info is None:
            raise ValueError(f"会话 {session_id} 的容器不存在")

        container = self.container_manager.client.containers.get(info.id)
        # 确保目标目录存在
        r = container.exec_run(["mkdir", "-p", container_dir])
        if r.exit_code != 0:
            raise RuntimeError(f"创建容器目录失败: {container_dir}")

        container.put_archive(container_dir, tar_stream.read())

    def _parse_execution_output(
        self,
        exec_result: dict,
        request: ToolExecutionRequest,
        cont_info,
        start_time: float,
    ) -> ExecutionResult:
        """解析容器执行输出为 ExecutionResult"""
        if exec_result["exit_code"] != 0:
            return ExecutionResult.fail(
                error=f"工具执行退出码: {exec_result['exit_code']}",
                logs={
                    "stdout": exec_result["stdout"],
                    "stderr": exec_result["stderr"],
                },
            )

        try:
            result_data = json.loads(exec_result["stdout"])
            if result_data.get("success"):
                return ExecutionResult.ok(
                    data=result_data.get("result"),
                    metadata={
                        "execution_time": time.time() - start_time,
                        "tool_name": request.tool_name,
                        "session_id": request.session_id,
                        "container_id": cont_info.id,
                    },
                )
            else:
                error_msg = result_data.get("error", "未知错误")
                error_type = error_msg.split(":")[0] if ": " in error_msg else None
                return ExecutionResult.fail(
                    error=error_msg,
                    logs={
                        "error_type": error_type,
                        "traceback": result_data.get("traceback", ""),
                        "stdout": exec_result["stdout"],
                        "stderr": exec_result["stderr"],
                    },
                )
        except json.JSONDecodeError as e:
            return ExecutionResult.fail(
                error=f"解析工具输出失败: {e}",
                logs={
                    "stdout": exec_result["stdout"],
                    "stderr": exec_result["stderr"],
                },
            )

    def _parse_run_output(
        self,
        exec_result: dict,
        session_id: str,
        cont_info,
        start_time: float,
    ) -> ExecutionResult:
        """解析 run() 的执行输出"""
        if exec_result["exit_code"] != 0:
            return ExecutionResult.fail(
                error=f"代码执行失败 (exit={exec_result['exit_code']})",
                logs={
                    "stdout": exec_result["stdout"],
                    "stderr": exec_result["stderr"],
                },
            )

        try:
            result_data = json.loads(exec_result["stdout"])
            if result_data.get("success"):
                return ExecutionResult.ok(
                    data=result_data.get("result"),
                    metadata={
                        "execution_time": time.time() - start_time,
                        "session_id": session_id,
                        "container_id": cont_info.id,
                    },
                )
            else:
                error_msg = result_data.get("error", "未知错误")
                error_type = error_msg.split(":")[0] if ": " in error_msg else None
                return ExecutionResult.fail(
                    error=error_msg,
                    logs={
                        "error_type": error_type,
                        "traceback": result_data.get("traceback", ""),
                        "stdout": exec_result["stdout"],
                        "stderr": exec_result["stderr"],
                    },
                )
        except json.JSONDecodeError as e:
            return ExecutionResult.fail(
                error=f"解析执行输出失败: {e}",
                logs={
                    "stdout": exec_result["stdout"],
                    "stderr": exec_result["stderr"],
                },
            )


# ── Harness 代码生成 ───────────────────────────────────────


def _generate_harness(tool_code: str, container_args_path: str) -> str:
    """生成包装代码，用于在容器内安全执行工具并输出 JSON 结果"""
    return f'''import json
import os
import sys
import traceback

def safe_execute_tool(args):
    """安全执行工具函数"""
    if "execute_tool" not in globals():
        return {{"success": False, "error": "工具缺少 execute_tool 函数"}}
    try:
        result = execute_tool(**args)
        return {{"success": True, "result": result}}
    except Exception as e:
        return {{
            "success": False,
            "error": f"{{type(e).__name__}}: {{e}}",
            "traceback": traceback.format_exc(),
        }}

{tool_code}

if __name__ == "__main__":
    try:
        os.chdir("/app/workspace")
        with open("{container_args_path}", "r", encoding="utf-8") as f:
            args = json.load(f)
        result = safe_execute_tool(args)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as e:
        error_output = {{
            "success": False,
            "error": f"Harness异常: {{type(e).__name__}}: {{e}}",
            "traceback": traceback.format_exc(),
        }}
        print(json.dumps(error_output, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
'''


def _generate_run_harness(code: str) -> str:
    """生成 run() 用的 harness — 直接执行代码并捕获输出"""
    return f'''import json
import sys
import traceback
import os
from io import StringIO

os.chdir("/app/workspace")

_code = {repr(code)}
_stdout = StringIO()
_real_stdout = sys.stdout  # 保存真实 stdout
sys.stdout = _stdout

try:
    exec(_code, {{"__name__": "__main__"}})
    _captured = _stdout.getvalue()
    result = {{"success": True, "result": _captured}}
except Exception as e:
    result = {{
        "success": False,
        "error": f"{{type(e).__name__}}: {{e}}",
        "traceback": traceback.format_exc(),
    }}

sys.stdout = _real_stdout  # 恢复 stdout
print(json.dumps(result, ensure_ascii=False, default=str))
'''
