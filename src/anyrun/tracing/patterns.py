"""模式识别 — 从执行轨迹中发现错误模式、成功路径和异常

纯统计 + 规则，不需要 LLM。
"""

import hashlib
import json
import os
import time
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from .store import TraceStore


# ── 数据模型 ──────────────────────────────────────────────


@dataclass
class Pattern:
    """一个被发现的执行模式"""

    pattern_id: str = ""
    type: str = ""  # error_cluster | success_path | anomaly
    signature: str = ""  # 特征签名
    description: str = ""
    occurrences: int = 0
    affected_sessions: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    sample_trace_ids: list = field(default_factory=list)
    status: str = "active"  # active | decayed | resolved

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Pattern":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── 分析器 ────────────────────────────────────────────────


class PatternAnalyzer:
    """从 TraceStore 中分析并发现模式"""

    def __init__(self, store: Optional[TraceStore] = None):
        self.store = store or TraceStore()

    # ── 错误聚类 ──────────────────────────────────────────

    def find_error_clusters(self, min_occurrences: int = 3) -> list[Pattern]:
        """聚类相同类型的错误"""
        with self.store._conn() as conn:
            rows = conn.execute("""
                SELECT error_type, COUNT(*) as cnt,
                       GROUP_CONCAT(trace_id) as trace_ids,
                       MIN(start_time), MAX(start_time),
                       COUNT(DISTINCT session_id)
                FROM traces
                WHERE success = 0 AND error_type IS NOT NULL
                GROUP BY error_type
                HAVING cnt >= ?
                ORDER BY cnt DESC
            """, (min_occurrences,)).fetchall()

        patterns = []
        for row in rows:
            error_type, count, trace_ids_str, first, last, sessions = row
            trace_ids = trace_ids_str.split(",")[:5]  # 最多保留 5 个样本

            p = Pattern(
                pattern_id=self._make_id(f"err_{error_type}"),
                type="error_cluster",
                signature=f"error:{error_type}",
                description=f"重复错误: {error_type}（{count}次，{sessions}个会话）",
                occurrences=count,
                affected_sessions=sessions,
                first_seen=first,
                last_seen=last,
                sample_trace_ids=trace_ids,
                status="active",
            )
            patterns.append(p)

        return patterns

    # ── 成功路径 ──────────────────────────────────────────

    def find_success_paths(self, min_occurrences: int = 5) -> list[Pattern]:
        """找高频成功执行的代码模式（按 input_code_hash 分组）"""
        with self.store._conn() as conn:
            rows = conn.execute("""
                SELECT input_code_hash, COUNT(*) as cnt,
                       GROUP_CONCAT(trace_id) as trace_ids,
                       MIN(start_time), MAX(start_time),
                       COUNT(DISTINCT session_id),
                       AVG(duration_ms)
                FROM traces
                WHERE success = 1 AND input_code_hash != ''
                GROUP BY input_code_hash
                HAVING cnt >= ?
                ORDER BY cnt DESC
                LIMIT 20
            """, (min_occurrences,)).fetchall()

        patterns = []
        for row in rows:
            code_hash, count, trace_ids_str, first, last, sessions, avg_dur = row
            trace_ids = trace_ids_str.split(",")[:5]

            # 从一条样本 trace 中读取实际代码
            sample_code = self._get_code_by_hash(code_hash)

            p = Pattern(
                pattern_id=self._make_id(f"ok_{code_hash}"),
                type="success_path",
                signature=f"code_hash:{code_hash}",
                description=(
                    f"高频成功: {sample_code[:60]}... "
                    f"（{count}次, 平均{avg_dur:.0f}ms）"
                ),
                occurrences=count,
                affected_sessions=sessions,
                first_seen=first,
                last_seen=last,
                sample_trace_ids=trace_ids,
                status="active",
            )
            patterns.append(p)

        return patterns

    # ── 异常检测 ──────────────────────────────────────────

    def find_anomalies(self, z_score_threshold: float = 3.0) -> list[Pattern]:
        """检测偏离正常的执行：耗时异常、输出异常"""
        with self.store._conn() as conn:
            # 计算 duration 的均值与标准差
            stats = conn.execute(
                "SELECT AVG(duration_ms), AVG(duration_ms * duration_ms) - AVG(duration_ms) * AVG(duration_ms) FROM traces WHERE success = 1"
            ).fetchone()
            avg_dur, var_dur = stats
            if var_dur is None or var_dur <= 0:
                return []
            std_dur = var_dur**0.5

            # 找超过阈值的
            threshold = avg_dur + z_score_threshold * std_dur
            rows = conn.execute(
                """SELECT trace_id, duration_ms, session_id, start_time
                   FROM traces
                   WHERE success = 1 AND duration_ms > ?
                   ORDER BY duration_ms DESC LIMIT 10""",
                (threshold,),
            ).fetchall()

        patterns = []
        for row in rows:
            tid, dur, sid, st = row
            p = Pattern(
                pattern_id=self._make_id(f"anom_{tid}"),
                type="anomaly",
                signature=f"duration>{threshold:.0f}ms",
                description=(
                    f"执行异常慢: {dur:.0f}ms "
                    f"（正常均值 {avg_dur:.0f}ms, 阈值 {threshold:.0f}ms）"
                ),
                occurrences=1,
                affected_sessions=1,
                first_seen=st,
                last_seen=st,
                sample_trace_ids=[tid],
                status="active",
            )
            patterns.append(p)

        return patterns

    # ── 全量分析 ──────────────────────────────────────────

    def analyze(self) -> dict:
        """一次运行所有分析器，返回分类结果"""
        return {
            "error_clusters": [p.to_dict() for p in self.find_error_clusters()],
            "success_paths": [p.to_dict() for p in self.find_success_paths()],
            "anomalies": [p.to_dict() for p in self.find_anomalies()],
            "analyzed_at": time.time(),
            "total_traces": self.store.count(),
        }

    # ── 辅助 ──────────────────────────────────────────────

    def _make_id(self, seed: str) -> str:
        return hashlib.md5(seed.encode()).hexdigest()[:12]

    def _get_code_by_hash(self, code_hash: str) -> str:
        """从 JSON 文件中读取匹配 code_hash 的第一条 trace 的代码"""
        json_dir = self.store.json_dir
        if not json_dir.exists():
            return ""

        for f in json_dir.glob("*.json"):
            try:
                with open(f) as fh:
                    d = json.load(fh)
                if d.get("input_code_hash") == code_hash:
                    return d.get("input_code", "")
            except Exception:
                continue
        return ""


# ── 模式存储 ──────────────────────────────────────────────


class PatternStore:
    """模式持久化"""

    def __init__(self, base_dir: Optional[str] = None):
        if base_dir is None:
            base_dir = os.path.join(os.path.expanduser("~"), ".anyrun", "traces")
        self.patterns_dir = Path(base_dir) / "patterns"
        self.patterns_dir.mkdir(parents=True, exist_ok=True)

    def save(self, pattern: Pattern):
        path = self.patterns_dir / f"{pattern.pattern_id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(pattern.to_dict(), f, ensure_ascii=False, indent=2)

    def save_all(self, patterns: list[Pattern]):
        for p in patterns:
            self.save(p)

    def load(self, pattern_id: str) -> Optional[Pattern]:
        path = self.patterns_dir / f"{pattern_id}.json"
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return Pattern.from_dict(json.load(f))

    def list(self) -> list[Pattern]:
        patterns = []
        for f in sorted(self.patterns_dir.glob("*.json"), key=os.path.getmtime, reverse=True):
            with open(f, "r", encoding="utf-8") as fh:
                patterns.append(Pattern.from_dict(json.load(fh)))
        return patterns

    def remove(self, pattern_id: str):
        path = self.patterns_dir / f"{pattern_id}.json"
        if path.exists():
            path.unlink()

    def clear(self):
        for f in self.patterns_dir.glob("*.json"):
            f.unlink()
