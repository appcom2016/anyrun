"""执行轨迹数据模型"""

from dataclasses import dataclass, field, asdict
from typing import Any, Optional
import uuid


@dataclass
class ExecutionTrace:
    """单次沙箱执行的完整记录"""

    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    session_id: str = "default"
    tool_name: str = "sandbox.run"

    # 输入
    input_code: str = ""
    input_code_hash: str = ""  # SHA256 前 16 位

    # 时序
    start_time: float = 0.0
    end_time: float = 0.0

    # 结果
    success: bool = False
    result_data: Optional[str] = None
    error_message: Optional[str] = None
    error_type: Optional[str] = None
    traceback: Optional[str] = None

    # 环境
    container_id: str = ""
    container_image: str = ""
    timeout: int = 60

    @property
    def duration_ms(self) -> float:
        return round((self.end_time - self.start_time) * 1000, 1)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration_ms"] = self.duration_ms
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ExecutionTrace":
        valid_fields = set(cls.__dataclass_fields__.keys())
        return cls(**{k: v for k, v in d.items() if k in valid_fields})

    def summary(self) -> str:
        status = "✓" if self.success else "✗"
        code_preview = self.input_code[:60].replace("\n", " ")
        return (
            f"[{status}] {self.trace_id} | {self.session_id} | "
            f"{self.duration_ms}ms | {code_preview}"
        )
