"""进化引擎 — 编排 skill 生命周期追踪、检测和修复"""

import os
from typing import Optional

from .lifecycle import SkillLifecycle, SkillStatus
from .tracker import EvolutionTracker, get_tracker
from .repair import AutoRepair, repair_all_decayed


class EvolutionEngine:
    """自进化总控"""

    def __init__(
        self,
        tracker: Optional[EvolutionTracker] = None,
        api_key: Optional[str] = None,
    ):
        self.tracker = tracker or get_tracker()
        self.api_key = api_key if api_key is not None else os.environ.get("DEEPSEEK_API_KEY", "")

    def record_skill_use(self, name: str, success: bool, session_id: str = "", trace_id: str = ""):
        """记录一次 skill 使用"""
        self.tracker.record_run(name, success, session_id, trace_id)

    def check_and_repair(self) -> dict:
        """检查所有 skill 状态，尝试修复退化的"""
        stats = self.tracker.stats()

        # 检查退化
        decayed = self.tracker.get_decayed()
        if not decayed:
            return {"checked": stats["total"], "repaired": 0, "decayed": 0}

        # 尝试修复
        results = repair_all_decayed(self.tracker, self.api_key)
        results["checked"] = stats["total"]
        return results

    def stats(self) -> dict:
        """进化统计"""
        return self.tracker.stats()

    def lifecycle(self, name: str) -> Optional[SkillLifecycle]:
        return self.tracker.get(name)


# 全局单例
_engine: Optional[EvolutionEngine] = None


def get_engine() -> EvolutionEngine:
    global _engine
    if _engine is None:
        _engine = EvolutionEngine()
    return _engine


def record_skill_run(skill_name: str, success: bool, session_id: str = "", trace_id: str = ""):
    """便捷函数：记录 skill 执行"""
    get_engine().record_skill_use(skill_name, success, session_id, trace_id)
