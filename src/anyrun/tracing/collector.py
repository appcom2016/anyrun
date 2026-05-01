"""跟踪采集器 — 在每次 Sandbox 执行后自动生成 ExecutionTrace"""

import hashlib
import time
from typing import Optional

from .models import ExecutionTrace
from .store import TraceStore
from .patterns import Pattern


class TraceCollector:
    """轻量采集器。不与 Sandbox 耦合，由 Sandbox.run() 主动调用。"""

    def __init__(self, store: Optional[TraceStore] = None):
        self.store = store or TraceStore()

    def collect(
        self,
        *,
        session_id: str = "default",
        code: str = "",
        container_id: str = "",
        container_image: str = "",
        timeout: int = 60,
        success: bool = False,
        result_data: Optional[str] = None,
        error_message: Optional[str] = None,
        error_type: Optional[str] = None,
        traceback: Optional[str] = None,
        start_time: float = 0.0,
        end_time: Optional[float] = None,
    ) -> ExecutionTrace:
        """采集一次执行的全部信息，返回 Trace"""
        if end_time is None:
            end_time = time.time()

        code_hash = hashlib.sha256(code.encode()).hexdigest()[:16]

        trace = ExecutionTrace(
            session_id=session_id,
            input_code=code,
            input_code_hash=code_hash,
            start_time=start_time,
            end_time=end_time,
            success=success,
            result_data=result_data,
            error_message=error_message,
            error_type=error_type,
            traceback=traceback,
            container_id=container_id,
            container_image=container_image,
            timeout=timeout,
        )

        self.store.save(trace)

        # 自动分析：每 50 条新 trace，后台异步触发一次
        count = self.store.count()
        if count > 0 and count % 50 == 0:
            import threading
            t = threading.Thread(target=self._run_analysis, daemon=True)
            t.start()

        # 自动清理：每 200 条检查一次，超过 10000 条时清理
        if count > 0 and count % 200 == 0:
            self.store.cleanup(max_traces=10000)

        return trace

    @staticmethod
    def _run_analysis():
        """后台执行模式分析（不阻塞主流程）"""
        try:
            from .patterns import PatternAnalyzer, PatternStore, Pattern
            store = get_store()
            analyzer = PatternAnalyzer(store)
            results = analyzer.analyze()
            pstore = PatternStore()
            pstore.clear()
            for category in ["error_clusters", "success_paths", "anomalies"]:
                for p_dict in results[category]:
                    pstore.save(Pattern.from_dict(p_dict))
        except Exception:
            pass


# 全局单例
_collector: Optional[TraceCollector] = None


def get_collector() -> TraceCollector:
    global _collector
    if _collector is None:
        _collector = TraceCollector()
    return _collector


def get_store() -> TraceStore:
    return get_collector().store
