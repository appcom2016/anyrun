"""技能生命周期管理 — beta → prod → decayed → retired"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class SkillStatus(str, Enum):
    BETA = "beta"
    PROD = "prod"
    DECAYED = "decayed"
    RETIRED = "retired"


# 生命周期规则
class LifecycleRules:
    # 升级条件
    PROMOTE_SUCCESS_COUNT = 20       # 连续成功 N 次 → prod
    PROMOTE_MIN_SESSIONS = 3         # 至少被 N 个不同 session 使用

    # 退化条件
    DECAY_WINDOW = 10                # 最近 N 次执行
    DECAY_SUCCESS_RATE = 0.8         # 成功率低于此 → decayed

    # 退役条件
    RETIRE_DAYS_DECAYED = 30         # 连续 decayed N 天 → retired

    # 修复条件
    REPAIR_MIN_FAILURES = 3          # 至少 N 次失败才尝试修复
    REPAIR_MAX_ATTEMPTS = 3          # 最多尝试修复 N 次


@dataclass
class SkillLifecycle:
    """跟踪单个 skill 的生命周期"""

    name: str
    status: SkillStatus = SkillStatus.BETA
    version: int = 1
    created_at: float = field(default_factory=time.time)
    promoted_at: Optional[float] = None
    decayed_at: Optional[float] = None
    repair_attempts: int = 0
    last_repair_at: Optional[float] = None

    # 统计
    total_runs: int = 0
    total_success: int = 0
    recent_runs: list = field(default_factory=list)  # [(success: bool, session_id), ...]
    sessions_used: set = field(default_factory=set)

    def record_run(self, success: bool, session_id: str = ""):
        """记录一次执行"""
        self.total_runs += 1
        if success:
            self.total_success += 1
        if session_id:
            self.sessions_used.add(session_id)

        # 维护滑动窗口
        self.recent_runs.append((success, session_id))
        max_window = max(
            LifecycleRules.DECAY_WINDOW,
            LifecycleRules.PROMOTE_SUCCESS_COUNT,
        )
        if len(self.recent_runs) > max_window:
            self.recent_runs = self.recent_runs[-max_window:]

        # 状态变迁
        self._evaluate()

    def _evaluate(self):
        """根据规则评估状态变迁"""
        rules = LifecycleRules

        if self.status == SkillStatus.BETA:
            # 检查升级条件
            recent = self.recent_runs[-rules.PROMOTE_SUCCESS_COUNT:]
            if len(recent) >= rules.PROMOTE_SUCCESS_COUNT:
                if all(s for s, _ in recent) and len(self.sessions_used) >= rules.PROMOTE_MIN_SESSIONS:
                    self.status = SkillStatus.PROD
                    self.promoted_at = time.time()

        elif self.status == SkillStatus.PROD:
            # 检查退化条件
            recent = self.recent_runs[-rules.DECAY_WINDOW:]
            if len(recent) >= rules.DECAY_WINDOW:
                success_count = sum(1 for s, _ in recent if s)
                rate = success_count / len(recent)
                if rate < rules.DECAY_SUCCESS_RATE:
                    self.status = SkillStatus.DECAYED
                    self.decayed_at = time.time()

        elif self.status == SkillStatus.DECAYED:
            # 检查是否恢复到 prod（自动修复成功）
            recent = self.recent_runs[-rules.DECAY_WINDOW:]
            if len(recent) >= rules.DECAY_WINDOW:
                success_count = sum(1 for s, _ in recent if s)
                rate = success_count / len(recent)
                if rate >= rules.DECAY_SUCCESS_RATE:
                    self.status = SkillStatus.PROD
                    self.decayed_at = None

            # 检查退役条件
            if self.decayed_at:
                days = (time.time() - self.decayed_at) / 86400
                if days > rules.RETIRE_DAYS_DECAYED:
                    self.status = SkillStatus.RETIRED

    @property
    def success_rate(self) -> float:
        if self.total_runs == 0:
            return 1.0
        return self.total_success / self.total_runs

    @property
    def recent_success_rate(self) -> float:
        recent = self.recent_runs[-LifecycleRules.DECAY_WINDOW:]
        if not recent:
            return 1.0
        return sum(1 for s, _ in recent if s) / len(recent)

    def needs_repair(self) -> bool:
        """是否需要自动修复"""
        if self.status != SkillStatus.DECAYED:
            return False
        if self.repair_attempts >= LifecycleRules.REPAIR_MAX_ATTEMPTS:
            return False
        recent = self.recent_runs[-LifecycleRules.DECAY_WINDOW:]
        failures = sum(1 for s, _ in recent if not s)
        return failures >= LifecycleRules.REPAIR_MIN_FAILURES

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status.value,
            "version": self.version,
            "total_runs": self.total_runs,
            "total_success": self.total_success,
            "success_rate": round(self.success_rate * 100, 1),
            "recent_rate": round(self.recent_success_rate * 100, 1),
            "sessions": len(self.sessions_used),
            "repair_attempts": self.repair_attempts,
            "needs_repair": self.needs_repair(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SkillLifecycle":
        lc = cls(name=d["name"])
        lc.status = SkillStatus(d.get("status", "beta"))
        lc.version = d.get("version", 1)
        lc.total_runs = d.get("total_runs", 0)
        lc.total_success = d.get("total_success", 0)
        lc.repair_attempts = d.get("repair_attempts", 0)
        return lc
