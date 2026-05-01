"""执行轨迹持久化 — SQLite 索引 + JSON 文件"""

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

from .models import ExecutionTrace


class TraceStore:
    """管理执行轨迹的存储和查询。

    每条 trace 存为 JSON 文件，SQLite 建索引加速查询。
    存储目录：~/.anyrun/traces/
    """

    def __init__(self, base_dir: Optional[str] = None):
        if base_dir is None:
            base_dir = os.path.join(os.path.expanduser("~"), ".anyrun", "traces")
        self.base_dir = Path(base_dir)
        self.json_dir = self.base_dir / "data"
        self.db_path = self.base_dir / "index.db"

        self.json_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── 数据库初始化 ───────────────────────────────────────

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS traces (
                    trace_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    tool_name TEXT DEFAULT 'sandbox.run',
                    success INTEGER NOT NULL DEFAULT 0,
                    error_type TEXT,
                    duration_ms REAL,
                    container_id TEXT,
                    start_time REAL,
                    input_code_hash TEXT,
                    json_path TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_session ON traces(session_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_success ON traces(success)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_error ON traces(error_type)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_time ON traces(start_time)"
            )
            conn.commit()

    def _conn(self):
        return sqlite3.connect(str(self.db_path))

    # ── 写入 ───────────────────────────────────────────────

    def save(self, trace: ExecutionTrace) -> str:
        """保存一条轨迹，返回 trace_id"""
        # 写入 JSON
        json_name = f"{trace.trace_id}.json"
        json_path = self.json_dir / json_name
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(trace.to_dict(), f, ensure_ascii=False, indent=2)

        # 写入 SQLite 索引
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO traces
                   (trace_id, session_id, tool_name, success, error_type,
                    duration_ms, container_id, start_time, input_code_hash, json_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trace.trace_id,
                    trace.session_id,
                    trace.tool_name,
                    1 if trace.success else 0,
                    trace.error_type,
                    trace.duration_ms,
                    trace.container_id,
                    trace.start_time,
                    trace.input_code_hash,
                    str(json_path),
                ),
            )
            conn.commit()

        return trace.trace_id

    # ── 查询 ───────────────────────────────────────────────

    def get(self, trace_id: str) -> Optional[ExecutionTrace]:
        """按 ID 获取完整轨迹"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT json_path FROM traces WHERE trace_id = ?", (trace_id,)
            ).fetchone()

        if row is None:
            return None

        json_path = row[0]
        if not os.path.exists(json_path):
            return None

        with open(json_path, "r", encoding="utf-8") as f:
            return ExecutionTrace.from_dict(json.load(f))

    def list(
        self,
        session_id: Optional[str] = None,
        success_only: bool = False,
        error_only: bool = False,
        error_type: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict]:
        """列出轨迹摘要（不加载完整 JSON）"""
        conditions = []
        params = []

        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        if success_only:
            conditions.append("success = 1")
        if error_only:
            conditions.append("success = 0")
        if error_type:
            conditions.append("error_type = ?")
            params.append(error_type)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        query = f"""
            SELECT trace_id, session_id, tool_name, success, error_type,
                   duration_ms, start_time
            FROM traces
            {where}
            ORDER BY start_time DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()

        return [
            {
                "trace_id": r[0],
                "session_id": r[1],
                "tool_name": r[2],
                "success": bool(r[3]),
                "error_type": r[4],
                "duration_ms": r[5],
                "start_time": r[6],
            }
            for r in rows
        ]

    def stats(self) -> dict:
        """基础统计"""
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
            success = conn.execute(
                "SELECT COUNT(*) FROM traces WHERE success = 1"
            ).fetchone()[0]
            failed = total - success

            avg_dur = conn.execute(
                "SELECT AVG(duration_ms) FROM traces"
            ).fetchone()[0]

            top_errors = conn.execute(
                """SELECT error_type, COUNT(*) as cnt
                   FROM traces WHERE error_type IS NOT NULL
                   GROUP BY error_type ORDER BY cnt DESC LIMIT 5"""
            ).fetchall()

            recent_sessions = conn.execute(
                """SELECT session_id, COUNT(*) as cnt
                   FROM traces GROUP BY session_id
                   ORDER BY MAX(start_time) DESC LIMIT 5"""
            ).fetchall()

            avg_dur = conn.execute(
                "SELECT AVG(duration_ms) FROM traces"
            ).fetchone()[0]

        return {
            "total": total,
            "success": success,
            "failed": failed,
            "success_rate": round(success / total * 100, 1) if total > 0 else 0,
            "avg_duration_ms": round(avg_dur, 1) if avg_dur is not None else 0,
            "top_errors": [{"type": e[0], "count": e[1]} for e in top_errors],
            "recent_sessions": [
                {"session_id": s[0], "traces": s[1]} for s in recent_sessions
            ],
        }

    def count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
