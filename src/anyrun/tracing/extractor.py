"""经验提取器 — 从执行模式中自动提炼可复用的 SKILL.md

Phase 2 核心：调用 LLM 将一组相关 traces 提炼为结构化经验文档。
"""

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .patterns import Pattern, PatternStore
from .store import TraceStore


# ── SKILL.md 模型 ────────────────────────────────────────


@dataclass
class ExtractedSkill:
    """LLM 提炼出的经验条目"""

    name: str
    description: str
    version: str = "1.0.0"
    source_pattern_id: str = ""
    source_pattern_type: str = ""
    steps: list = field(default_factory=list)
    pitfalls: list = field(default_factory=list)
    trigger_condition: str = ""
    raw_markdown: str = ""

    def to_skill_md(self) -> str:
        """生成完整的 SKILL.md 内容"""
        lines = [
            "---",
            f"name: {self.name}",
            f"description: {self.description}",
            f"version: {self.version}",
            f"source: auto_extracted",
            f"pattern_id: {self.source_pattern_id}",
            "---",
            "",
            f"# {self.name.replace('-', ' ').title()}",
            "",
            f"**自动提取的经验** — 来源于 {self.source_pattern_type} 模式的 {len(self.steps)} 个样本执行。",
            "",
            "## 触发条件",
            "",
            self.trigger_condition or "_（从执行模式中自动识别）_",
            "",
        ]

        if self.steps:
            lines.append("## 步骤")
            lines.append("")
            for i, step in enumerate(self.steps, 1):
                lines.append(f"{i}. {step}")
            lines.append("")

        if self.pitfalls:
            lines.append("## 常见陷阱")
            lines.append("")
            for p in self.pitfalls:
                lines.append(f"- {p}")
            lines.append("")

        return "\n".join(lines)

    @classmethod
    def from_markdown(cls, md: str, pattern: Pattern) -> Optional["ExtractedSkill"]:
        """从 LLM 返回的 markdown 解析出 ExtractedSkill"""
        # 解析 frontmatter
        frontmatter = {}
        if md.startswith("---"):
            parts = md.split("---", 2)
            if len(parts) >= 3:
                for line in parts[1].strip().split("\n"):
                    if ":" in line:
                        k, v = line.split(":", 1)
                        frontmatter[k.strip()] = v.strip()

        name = frontmatter.get("name", "")
        description = frontmatter.get("description", pattern.description)

        # 如果 LLM 生成的名称仍然是 auto-skill-xxx，用 pattern 信息生成更好的名称
        if not name or name.startswith("auto-skill-"):
            name = _derive_skill_name(pattern, md)

        # 提取步骤（匹配 "1. xxx" 格式的行）
        steps = []
        pitfalls = []
        trigger = ""
        in_steps = False
        in_pitfalls = False
        in_trigger = False

        for line in md.split("\n"):
            line = line.strip()
            if "## 步骤" in line or "## Steps" in line:
                in_steps = True
                in_pitfalls = False
                in_trigger = False
                continue
            if "陷阱" in line or "Pitfalls" in line or "## 常见" in line:
                in_pitfalls = True
                in_steps = False
                in_trigger = False
                continue
            if "触发" in line or "When to Use" in line or "## 触发" in line:
                in_trigger = True
                in_steps = False
                in_pitfalls = False
                continue
            if line.startswith("##") or line.startswith("#"):
                in_steps = in_pitfalls = in_trigger = False
                continue

            if in_steps and re.match(r"^\d+[\.\)]\s", line):
                steps.append(re.sub(r"^\d+[\.\)]\s*", "", line))
            elif in_pitfalls and line.startswith("- "):
                pitfalls.append(line[2:])
            elif in_trigger and line and not line.startswith("_"):
                if trigger:
                    trigger += " " + line
                else:
                    trigger = line

        return cls(
            name=name,
            description=description,
            source_pattern_id=pattern.pattern_id,
            source_pattern_type=pattern.type,
            steps=steps,
            pitfalls=pitfalls,
            trigger_condition=trigger,
            raw_markdown=md,
        )


def _derive_skill_name(pattern: Pattern, md: str) -> str:
    """从 pattern 描述和 markdown 内容推导有意义的 skill 名称"""
    desc = pattern.description.lower()

    # 基于错误类型
    if "zerodivisionerror" in desc:
        return "python-zero-division-guard"
    if "valueerror" in desc:
        if "int(" in md or "int()" in md:
            return "python-int-safe-conversion"
        return "python-valueerror-guard"
    if "syntaxerror" in desc:
        return "python-syntax-correction"
    if "nameerror" in desc:
        return "python-undefined-variable-fix"
    if "filenotfounderror" in desc:
        return "shell-file-existence-check"
    if "permissionerror" in desc:
        return "shell-permission-handling"

    # 基于模式类型
    if pattern.type == "error_cluster":
        # 从描述中提取关键词
        words = desc.replace("重复错误:", "").strip().split()
        key = "-".join(w.lower()[:8] for w in words[:2])
        return f"error-guard-{key}"
    if pattern.type == "success_path":
        return f"success-pattern-{pattern.pattern_id[:8]}"
    if pattern.type == "anomaly":
        return f"anomaly-alert-{pattern.pattern_id[:8]}"

    return f"skill-{pattern.pattern_id[:8]}"


# ── 提取 Prompt ───────────────────────────────────────────

EXTRACTION_SYSTEM_PROMPT = """你是一个 AI Agent 的经验分析师。

你的任务：分析一组工具执行记录，从中提炼出**可复用的操作经验**，输出为 SKILL.md 格式。

## 输入格式

你会收到：
1. 模式类型（错误聚类 / 成功路径 / 异常）
2. 一组相关的执行记录（含代码、错误信息、堆栈）

## 输出格式

请严格按照以下 SKILL.md 格式输出。**name 字段必须是有意义的英文标识符**，不要用 ID 或数字编号。

```markdown
---
name: python-int-conversion-guard
description: 当 int() 转换可能包含非数字字符时，先用 isdigit() 或 try/except 防护
version: 1.0.0
---

# 技能标题

**自动提取的经验**

## 触发条件

什么情况下应该使用这个经验？具体描述触发场景。

## 步骤

1. 第一步具体操作（含代码示例）
2. 第二步具体操作
...

## 常见陷阱

- 容易出错的地方
- 需要注意的边界条件
```

## 命名规则（重要）

name 字段的格式：`<技术栈>-<场景>-<动作>`，例如：
- `python-int-safe-conversion` ✓
- `docker-pip-install-c-extensions` ✓
- `shell-mkdir-before-write` ✓
- `auto-skill-123` ✗ 不要用这种
- `skill-1` ✗ 不要用这种

## 规则

1. **只提取工具使用层面的经验**。不要提取业务逻辑相关的内容。
2. **经验必须具体、可直接操作**。包含代码示例。
3. **优先提取失败→成功的修复经验**。展示错误代码和修复后代码。
4. **成功路径同样重要**。提炼成功执行的关键条件。
5. **用中文撰写内容**，name 用英文。
"""


# ── 提取器 ────────────────────────────────────────────────


class ExperienceExtractor:
    """从执行模式中自动提炼经验"""

    def __init__(
        self,
        trace_store: Optional[TraceStore] = None,
        pattern_store: Optional[PatternStore] = None,
        api_key: Optional[str] = None,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-v4-flash",
    ):
        self.trace_store = trace_store or TraceStore()
        self.pattern_store = pattern_store or PatternStore()
        self.api_key = api_key if api_key is not None else os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = base_url
        self.model = model
        self.skills_dir = Path(os.path.expanduser("~/.anyrun/skills"))
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def extract_from_pattern(self, pattern: Pattern) -> Optional[ExtractedSkill]:
        """从一个 Pattern 中提取经验"""
        # 边界检查
        if pattern.occurrences < 2:
            print(f"  Pattern {pattern.pattern_id} has only {pattern.occurrences} occurrence(s), skip")
            return None

        # 1. 获取样本 traces
        samples = []
        for tid in pattern.sample_trace_ids[:5]:
            trace = self.trace_store.get(tid)
            if trace:
                samples.append(trace)

        if not samples:
            print(f"  No sample traces found for pattern {pattern.pattern_id}")
            return None

        # 2. 构建 prompt
        user_prompt = self._build_prompt(pattern, samples)

        # 3. 调用 LLM
        response = self._call_llm(user_prompt)

        if not response:
            return None

        # 4. 解析 SKILL.md
        skill = ExtractedSkill.from_markdown(response, pattern)

        if not skill:
            print(f"  Failed to parse LLM response for {pattern.pattern_id}")
            return None

        # 5. 保存
        self._save_skill(skill)

        return skill

    def _build_prompt(self, pattern: Pattern, samples: list) -> str:
        """构建发给 LLM 的 prompt"""
        parts = [
            f"## 模式信息",
            f"- 类型: {pattern.type}",
            f"- 描述: {pattern.description}",
            f"- 出现次数: {pattern.occurrences}",
            f"- 影响会话数: {pattern.affected_sessions}",
            "",
            "## 样本执行记录",
        ]

        for i, trace in enumerate(samples[:5]):
            status = "成功" if trace.success else "失败"
            parts.append(f"\n### 样本 {i+1} ({status}, {trace.duration_ms}ms)")
            parts.append(f"```python\n{trace.input_code.strip()}\n```")

            if trace.error_message:
                parts.append(f"错误: {trace.error_message}")
            if trace.result_data and trace.success:
                preview = trace.result_data[:300]
                parts.append(f"输出: {preview}")
            if trace.traceback:
                # 只保留最后几行（最关键的错误位置）
                tb_lines = trace.traceback.strip().split("\n")
                parts.append(f"关键堆栈:\n```\n" + "\n".join(tb_lines[-4:]) + "\n```")

        parts.append(
            "\n请从以上执行记录中提炼出可复用的经验，输出 SKILL.md 格式。"
        )

        return "\n".join(parts)

    def _call_llm(self, user_prompt: str) -> Optional[str]:
        """调用 LLM 生成 SKILL.md"""
        if not self.api_key:
            print("  No API key configured, skipping LLM call")
            return None

        try:
            from openai import OpenAI

            client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=2048,
            )
            content = response.choices[0].message.content
            # 后处理：提取 SKILL.md 部分
            if "---" in content:
                start = content.index("---")
                content = content[start:]
            return content
        except Exception as e:
            print(f"  LLM call failed: {e}")
            return None

    def _save_skill(self, skill: ExtractedSkill) -> str:
        """保存 SKILL.md 到磁盘（按名称建子目录）"""
        skill_dir = self.skills_dir / skill.name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"
        content = skill.to_skill_md()
        with open(skill_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  Saved: {skill_path}")
        return str(skill_path)

    def extract_all(self, min_occurrences: int = 3) -> list[ExtractedSkill]:
        """对所有符合条件的模式执行提取"""
        patterns = self.pattern_store.list()
        skills = []

        for pattern in patterns:
            if pattern.occurrences < min_occurrences:
                continue
            if pattern.status != "active":
                continue
            # 跳过异常模式（通常只有 1 次，不够提炼经验）
            if pattern.type == "anomaly":
                continue

            print(f"\n提取中: [{pattern.pattern_id}] {pattern.type}")
            skill = self.extract_from_pattern(pattern)
            if skill:
                skills.append(skill)
                # 标记模式为 resolved
                pattern.status = "resolved"
                self.pattern_store.save(pattern)

        return skills


# ── ToolRegistry 集成 ─────────────────────────────────────


def register_skill_to_registry(skill: ExtractedSkill, registry=None):
    """将提取的 skill 注册到 ToolRegistry"""
    if registry is None:
        from anyrun import ToolRegistry
        registry = ToolRegistry()

    from ..models import Skill
    skill_path = str(
        Path(os.path.expanduser("~/.anyrun/skills")) / skill.name
    )

    try:
        registry._skills[skill.name] = Skill(
            name=skill.name,
            description=skill.description,
            path=skill_path,
        )
        print(f"  Registered skill: {skill.name}")
    except Exception as e:
        print(f"  Register failed: {e}")
