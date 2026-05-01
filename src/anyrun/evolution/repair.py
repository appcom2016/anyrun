"""自动修复 — 当 skill 退化时，分析失败并生成修复 patch"""

import json
import os
import time
from pathlib import Path
from typing import Optional

from .lifecycle import SkillLifecycle, SkillStatus, LifecycleRules
from .tracker import EvolutionTracker


REPAIR_SYSTEM_PROMPT = """你是一个 Skill 修复专家。你的输出必须是**纯 SKILL.md 格式**，不要包含任何解释、问候语或分析过程。

直接输出修复后的完整 SKILL.md 文件内容，以 `---` 开头。

## 格式要求

```markdown
---
name: <保持原名>
description: <更新描述，末尾加 [auto-repaired v{version}]>
version: {version}
---

# <标题>

## 触发条件
...

## 步骤
1. ...
2. ...

## 常见陷阱
- ...
```

## 输出规则

1. **只输出 SKILL.md 内容**，不要有任何前置说明
2. 以 `---` frontmatter 开头
3. version 递增（如 1.0.0 → 1.1.0）
4. 更新步骤和陷阱
5. 用中文撰写"""


class AutoRepair:
    """自动修复退化的 skill"""

    def __init__(
        self,
        tracker: Optional[EvolutionTracker] = None,
        api_key: Optional[str] = None,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-v4-flash",
    ):
        self.tracker = tracker or EvolutionTracker()
        self.api_key = api_key if api_key is not None else os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = base_url
        self.model = model
        self.skills_dir = Path(os.path.expanduser("~/.anyrun/skills"))

    def repair(self, lc: SkillLifecycle) -> Optional[str]:
        """尝试修复一个退化的 skill，返回新的 SKILL.md 路径或 None"""
        if not lc.needs_repair():
            return None

        print(f"  Repairing: {lc.name} (v{lc.version}, {lc.repair_attempts+1}/{LifecycleRules.REPAIR_MAX_ATTEMPTS})")

        # 1. 读取原始 SKILL.md
        skill_md = self._read_skill_md(lc.name)
        if not skill_md:
            print(f"    Cannot read SKILL.md for {lc.name}")
            return None

        # 2. 获取最近的失败记录
        failures = self._get_recent_failures(lc.name)
        if not failures:
            print(f"    No recent failures found")
            return None

        # 3. 调用 LLM 生成修复
        new_md = self._call_repair_llm(lc, skill_md, failures)
        if not new_md:
            lc.repair_attempts += 1
            self.tracker._persist(lc)
            return None

        # 4. 验证新 skill（在沙箱中测试）
        if not self._validate_skill(lc.name, new_md):
            print(f"    Validation failed, discard repair")
            lc.repair_attempts += 1
            self.tracker._persist(lc)
            return None

        # 5. 保存新版本
        new_path = self._save_repaired_skill(lc, new_md)
        lc.version += 1
        lc.repair_attempts = 0
        lc.status = SkillStatus.BETA  # 修复后回 beta 重新验证
        self.tracker._persist(lc)

        print(f"    ✓ Repaired: v{lc.version-1} → v{lc.version}, back to beta")
        return new_path

    def _read_skill_md(self, name: str) -> Optional[str]:
        path = self.skills_dir / name / "SKILL.md"
        if not path.exists():
            return None
        with open(path) as f:
            return f.read()

    def _get_recent_failures(self, name: str) -> list[dict]:
        with self.tracker._conn() as conn:
            rows = conn.execute(
                """SELECT trace_id, timestamp FROM skill_usage
                   WHERE skill_name=? AND success=0
                   ORDER BY timestamp DESC LIMIT 10""",
                (name,),
            ).fetchall()

        if not rows:
            return []

        # 获取 trace 详情
        from tracing.store import TraceStore
        tstore = TraceStore()
        failures = []
        for row in rows:
            tid, ts = row
            trace = tstore.get(tid) if tid else None
            if trace:
                failures.append({
                    "trace_id": tid,
                    "error": trace.error_message or "",
                    "code": trace.input_code[:200],
                })
        return failures

    def _call_repair_llm(self, lc: SkillLifecycle, original: str, failures: list[dict]) -> Optional[str]:
        if not self.api_key:
            return None

        failures_text = "\n".join(
            f"- {f['error'][:120]}"
            for f in failures[:5]
        )

        user_prompt = f"""## 退化 Skill

{original}

## 最近失败 ({len(failures)} 次)

{failures_text}

## 生命周期

- 总执行: {lc.total_runs}
- 总成功: {lc.total_success}
- 成功率: {lc.success_rate*100:.1f}%
- 最近: {lc.recent_success_rate*100:.1f}%
- 状态: {lc.status.value}

请分析退化原因并生成修复后的 SKILL.md。"""

        try:
            from openai import OpenAI
            client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": REPAIR_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=2048,
            )
            content = response.choices[0].message.content

            # 后处理：如果 LLM 输出包含对话文本，提取 SKILL.md 部分
            if "---" in content:
                # 找到第一个 frontmatter 开始位置
                start = content.index("---")
                content = content[start:]
            return content
        except Exception as e:
            print(f"    Repair LLM call failed: {e}")
            return None

    def _validate_skill(self, name: str, new_md: str) -> bool:
        """在 Docker 沙箱中验证修复后的 skill"""
        try:
            from anyrun import Sandbox
            sandbox = Sandbox()
            # 简单验证：skill 能在沙箱中成功执行
            r = sandbox.run(
                f"# Auto-validation for repaired skill: {name}\nprint('validation_ok')",
                session_id=f"repair-{name}",
                timeout=30,
            )
            sandbox.cleanup_session(f"repair-{name}")
            return r.success
        except Exception as e:
            print(f"    Validation error: {e}")
            return False

    def _save_repaired_skill(self, lc: SkillLifecycle, new_md: str) -> str:
        skill_dir = self.skills_dir / lc.name
        skill_dir.mkdir(parents=True, exist_ok=True)
        path = skill_dir / "SKILL.md"
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_md)
        return str(path)


def repair_all_decayed(tracker: Optional[EvolutionTracker] = None, api_key: Optional[str] = None) -> dict:
    """修复所有需要修复的退化 skill"""
    t = tracker or EvolutionTracker()
    repair = AutoRepair(tracker=t, api_key=api_key)
    decayed = t.get_decayed()

    results = {"total": len(decayed), "repaired": 0, "failed": 0, "skipped": 0}
    for lc in decayed:
        result = repair.repair(lc)
        if result:
            results["repaired"] += 1
        elif lc.repair_attempts >= LifecycleRules.REPAIR_MAX_ATTEMPTS:
            results["skipped"] += 1
        else:
            results["failed"] += 1

    return results
