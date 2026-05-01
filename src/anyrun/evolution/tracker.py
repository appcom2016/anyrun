"""进化追踪器 — 记录 skill 生命周期和每次使用"""

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

from .lifecycle import SkillLifecycle, SkillStatus


class EvolutionTracker:
    """持久化 skill 生命周期和每次执行记录"""

    def __init__(self, base_dir: Optional[str] = None):
        if base_dir is None:
            base_dir = os.path.join(os.path.expanduser("~"), ".anyrun", "evolution")
        self.base_dir = Path(base_dir)
        self.db_path = self.base_dir / "evolution.db"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._cache: dict[str, SkillLifecycle] = {}
        self._load_cache()

    def _conn(self):
        return sqlite3.connect(str(self.db_path))

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS skill_lifecycle (
                    name TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'beta',
                    version INTEGER DEFAULT 1,
                    total_runs INTEGER DEFAULT 0,
                    total_success INTEGER DEFAULT 0,
                    repair_attempts INTEGER DEFAULT 0,
                    extra_json TEXT DEFAULT '{}'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS skill_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    skill_name TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    session_id TEXT DEFAULT '',
                    trace_id TEXT DEFAULT '',
                    timestamp REAL NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_name ON skill_usage(skill_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_time ON skill_usage(timestamp)"
            )
            conn.commit()

    def _load_cache(self):
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT name, status, version, total_runs, total_success, repair_attempts, extra_json FROM skill_lifecycle"
            ).fetchall()
        for row in rows:
            name, status, ver, runs, succ, repairs, extra = row
            lc = SkillLifecycle(name=name)
            lc.status = SkillStatus(status)
            lc.version = ver
            lc.total_runs = runs
            lc.total_success = succ
            lc.repair_attempts = repairs
            if extra:
                try:
                    d = json.loads(extra)
                    lc.promoted_at = d.get("promoted_at")
                    lc.decayed_at = d.get("decayed_at")
                    lc.sessions_used = set(d.get("sessions", []))
                except Exception:
                    pass

            # 加载最近的 usage 记录
            with self._conn() as conn:
                recents = conn.execute(
                    "SELECT success, session_id FROM skill_usage WHERE skill_name=? ORDER BY timestamp DESC LIMIT 50",
                    (name,),
                ).fetchall()
            lc.recent_runs = [(bool(r[0]), r[1]) for r in reversed(recents)]

            self._cache[name] = lc

    def get(self, name: str) -> SkillLifecycle:
        """获取或创建 skill 生命周期"""
        if name not in self._cache:
            self._cache[name] = SkillLifecycle(name=name)
            self._persist(self._cache[name])
        return self._cache[name]

    def record_run(self, name: str, success: bool, session_id: str = "", trace_id: str = ""):
        """记录一次 skill 使用"""
        lc = self.get(name)
        lc.record_run(success, session_id)

        # 记录 usage
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO skill_usage (skill_name, success, session_id, trace_id, timestamp) VALUES (?, ?, ?, ?, ?)",
                (name, 1 if success else 0, session_id, trace_id, time.time()),
            )
            conn.commit()

        # 持久化生命周期
        self._persist(lc)

    def _persist(self, lc: SkillLifecycle):
        extra = json.dumps({
            "promoted_at": lc.promoted_at,
            "decayed_at": lc.decayed_at,
            "sessions": list(lc.sessions_used),
        })
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO skill_lifecycle
                   (name, status, version, total_runs, total_success, repair_attempts, extra_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    lc.name, lc.status.value, lc.version,
                    lc.total_runs, lc.total_success,
                    lc.repair_attempts, extra,
                ),
            )
            conn.commit()

    def list_all(self) -> list[SkillLifecycle]:
        return list(self._cache.values())

    def get_decayed(self) -> list[SkillLifecycle]:
        return [lc for lc in self._cache.values() if lc.needs_repair()]

    def stats(self) -> dict:
        all_lc = self.list_all()
        return {
            "total": len(all_lc),
            "beta": sum(1 for lc in all_lc if lc.status == SkillStatus.BETA),
            "prod": sum(1 for lc in all_lc if lc.status == SkillStatus.PROD),
            "decayed": sum(1 for lc in all_lc if lc.status == SkillStatus.DECAYED),
            "retired": sum(1 for lc in all_lc if lc.status == SkillStatus.RETIRED),
            "skills": [lc.to_dict() for lc in all_lc],
        }


# 全局单例
_tracker: Optional[EvolutionTracker] = None


def get_tracker() -> EvolutionTracker:
    global _tracker
    if _tracker is None:
        _tracker = EvolutionTracker()
    return _tracker
