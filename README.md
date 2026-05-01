<p align="center">
  <img src="https://img.shields.io/badge/version-1.3.0-blue.svg" alt="Version 1.3.0">
  <img src="https://img.shields.io/badge/python-3.10+-brightgreen.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License">
  <img src="https://img.shields.io/badge/status-Beta-yellow.svg" alt="Status Beta">
  <img src="https://img.shields.io/badge/docker-required-2496ED.svg?logo=docker" alt="Docker Required">
</p>

<h1 align="center">📦 anyrun</h1>
<p align="center"><strong>AI Agent 的 Docker 沙箱执行引擎</strong><br>
内置全量观测、模式发现、经验提取与自进化闭环</p>

<p align="center">
  <code>pip install anyrun-agent</code>
</p>

---

## 概述

anyrun 是为 AI Agent 设计的**安全代码执行引擎**。它提供一个隔离的 Docker 沙箱环境，自动采集每一次执行的完整轨迹，通过统计规则发现模式，调用 LLM 提炼可复用的经验，最终实现技能的自动进化。

```
Agent 执行代码 → Docker 沙箱隔离执行
                    ↓
              全量轨迹采集（SQLite + JSON）
                    ↓
              模式识别（错误聚类 / 成功路径 / 异常检测）
                    ↓
              LLM 经验提取 → SKILL.md
                    ↓
              自进化（beta → prod → decayed → retired）
                    ↓
              注入回 Agent → 下次执行更可靠
```

### 适用场景

- **LLM Agent 工具执行** — 为 DeepSeek、GPT、Claude 等模型的安全代码执行提供沙箱
- **Agent 自我迭代** — 从执行经验中自动学习，无需人工编写文档
- **跨 Agent 经验沉淀** — 团队内共享工具使用的最佳实践
- **CI/CD 中的安全代码运行** — 隔离环境下运行不可信代码

---

## 安装

```bash
pip install anyrun-agent
```

**要求：**
- Python 3.10+
- Docker（macOS Docker Desktop / Linux Docker Engine）

**可选：** MCP Server 集成需 `mcp` SDK：

```bash
pip install anyrun-agent[mcp]
```

---

## 快速开始

### 基础用法：一行代码安全执行

```python
from anyrun import Sandbox

with Sandbox() as s:
    result = s.run("print(1 + 1)")
    print(result.data)  # "2\n"
```

### 多会话隔离

```python
sandbox = Sandbox()

# 不同 session 有独立的文件系统和命名空间
sandbox.run("x = 42", session_id="session-a")
sandbox.run("y = 100", session_id="session-b")

# 清理会话
sandbox.cleanup_session("session-a")
```

### 完整 CRUD 示例

```python
# 文件操作（会话内持久化）
sandbox.run('open("/app/workspace/data.txt", "w").write("hello")')
result = sandbox.run('print(open("/app/workspace/data.txt").read())')
# result.data → "hello\n"
```

### 工具管理

```python
from anyrun import ToolRegistry, Tool

registry = ToolRegistry()

# 查看内置工具
tools = registry.get_tools_info()
# [{"name": "shell", ...}, {"name": "create_file", ...}]

# 注册自定义工具
registry.add_tool(Tool(
    name="add_numbers",
    description="对两个数字求和",
    parameters={"a": {"type": "integer"}, "b": {"type": "integer"}},
    code="def execute_tool(a: int, b: int): return a + b",
))
```

---

## CLI 参考

### 系统管理

| 命令 | 说明 |
|------|------|
| `anyrun --version` | 显示版本号 |
| `anyrun version` | 显示版本详情 |
| `anyrun config` | 查看配置路径和数据存储位置 |

### 会话管理

```bash
# 列出所有活跃的 Docker 容器
anyrun session ls

# 停止会话容器
anyrun session cleanup

# 停止并删除容器
anyrun session cleanup --delete

# 清理指定会话
anyrun session cleanup --session-id mcp-default
```

### 执行轨迹

```bash
# 查看最近 20 条执行记录
anyrun traces ls

# 只看失败的
anyrun traces ls --errors

# 查看单条详情
anyrun traces show <trace_id>

# 统计概览
anyrun traces stats

# 手动触发数据清理
anyrun traces cleanup --max 10000
```

输出示例：
```
Total traces:     104
Successful:       74 (71.2%)
Failed:           30
Avg duration:     518.8ms

Top errors:
  ZeroDivisionError: 7x
  ValueError: 3x
```

### 模式发现

```bash
# 运行全量分析
anyrun patterns analyze

# 查看已发现的模式
anyrun patterns ls

# 查看模式详情
anyrun patterns show <pattern_id>
```

### 经验提取

```bash
# 从所有活跃模式中提取经验
anyrun extract

# 从指定模式中提取
anyrun extract --pattern-id <id>

# 查看生成的技能
ls ~/.anyrun/skills/
```

生成的 SKILL.md 示例：

```markdown
---
name: python-int-safe-conversion
description: 当 int() 转换可能包含非数字字符时的防护方案
version: 1.0.0
source: auto_extracted
---

## 触发条件
当需要将字符串或用户输入转换为整数，但输入可能包含非数字字符时。

## 步骤
1. 识别输入来源是否不可控
2. 使用 isdigit() 预检查或 try/except 包裹
3. 实现安全转换并处理异常
```

### 自进化

```bash
# 查看技能的进化状态
anyrun evolution stats

# 修复已退化的技能（LLM 驱动）
anyrun evolution repair
```

输出示例：
```
Skills tracked: 1
  beta:    1
  prod:    0
  decayed: 0
  retired: 0

  🔶 python-zero-division-guard (beta)
     runs=47, rate=71.7%, sessions=5
```

---

## MCP Server

anyrun 自带 MCP 服务器，可动态暴露 Toolbox 中的所有工具给 Hermes、Claude Desktop 等 MCP 客户端。

### 配置

在 `~/.hermes/config.yaml` 中：

```yaml
mcp_servers:
  anyrun:
    command: "/usr/local/bin/python3"
    args: ["-m", "anyrun.mcp_server"]
```

> ⚠️ MCP 使用绝对 Python 路径，确保 `docker` 和 `mcp` SDK 在对应环境中可用。

### 暴露的工具

| 分类 | 工具 | 说明 |
|------|------|------|
| 代码执行 | `sandbox_run` | Docker 沙箱中执行任意 Python |
| 轨迹管理 | `trace_list` | 列出执行轨迹 |
| | `trace_get` | 获取单条轨迹详情 |
| | `trace_stats` | 执行统计 |
| Toolbox 管理 | `toolbox_add_tool` | 注册新工具 |
| | `toolbox_get_tool` | 获取工具详情 |
| | `toolbox_update_tool_code` | 更新工具代码 |
| | `toolbox_promote_tool` | beta → prod |
| | `toolbox_delete_tool` | 删除工具 |
| | `toolbox_get_tools_info` | 工具摘要列表 |
| | `toolbox_get_tool_count` | 工具总数 |
| 技能管理 | `toolbox_get_skill` | 获取技能元数据 |
| | `toolbox_get_skills_info` | 技能列表 |
| | `toolbox_get_skills_prompt` | LLM 友好格式 |
| 会话管理 | `session_list` | 列出会话容器 |
| | `session_cleanup` | 清理会话 |
| 内置工具 | `shell` | Shell 命令执行 |
| | `create_file` | 文件创建 |
| | `file_read` | 文件读取 |
| | `dir_list` | 目录列表 |
| | `file_search` | 文件内容搜索 |
| | `ensure_dirs` | 目录创建 |

### Agent 自迭代循环

```
→ sandbox_run 调试代码片段
→ toolbox_add_tool 将能力抽象为可复用工具
→ toolbox_promote_tool 将稳定工具升为 prod
→ toolbox_update_tool_code 修复工具 bug
→ 工具在 MCP 客户端重启后自动生效
```

---

## API 参考

### Sandbox

```python
Sandbox(
    host_workspace_root=None,   # None → ~/.anyrun/workspace/
    docker_image="python:3.12-slim",
)
```

| 方法 | 说明 |
|------|------|
| `run(code, session_id, timeout, skill_name)` | 执行 Python 代码 → ExecutionResult |
| `execute_tool(request)` | 按 ToolExecutionRequest 执行工具 |
| `cleanup_session(session_id)` | 清理会话 Docker 容器 |
| `session_context(session_id)` | 上下文管理器，退出时自动清理 |

### ExecutionResult

```python
@dataclass
class ExecutionResult:
    success: bool          # 是否成功
    data: Any              # stdout 输出内容
    error: str | None      # 错误信息
    logs: dict | None      # stdout/stderr/traceback/error_type
    metadata: dict | None  # execution_time, container_id, session_id
```

### ToolRegistry

| 方法 | 说明 |
|------|------|
| `get_tools_info()` | 所有工具摘要（LLM 友好格式） |
| `get_tool(name)` | 获取单个工具代码及元数据 |
| `add_tool(tool)` | 注册新工具（同名自动递增版本） |
| `update_tool_code(name, code)` | 更新代码，重置为 beta |
| `promote_tool(name)` | 提升为 prod |
| `delete_tool(name)` | 删除工具 |
| `get_skills_info()` | 已加载技能列表 |
| `get_skills_prompt()` | LLM 友好的技能信息 |

---

## 数据存储

所有运行时数据统一存储在 `~/.anyrun/` 目录下：

```
~/.anyrun/
├── data/
│   └── toolbox.json         # 工具注册信息（v1.3.0 起）
├── traces/
│   ├── data/                # 执行轨迹 JSON 文件
│   ├── index.db             # SQLite 索引（快速查询）
│   └── patterns/            # 发现的模式
├── skills/                  # LLM 自动提取的技能
│   └── <name>/SKILL.md
└── evolution/
    └── evolution.db          # 技能生命周期数据
```

> **v1.3.0 变更：** 工具数据从包内路径迁移至 `~/.anyrun/data/toolbox.json`，不再写入 site-packages 目录。

### 数据清理

系统自动维护轨迹数据上限（默认 10,000 条），超出时自动删除最旧记录，无需手动干预。也可通过 CLI 手动触发：

```bash
anyrun traces cleanup --max 5000
```

---

## 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.3.0 | 2026-05 | Bug 修复：import 路径、数据持久化、并发安全；新增 CLI 命令、自动清理 |
| 1.2.0 | 2026-04 | Phase 3 完成：自进化闭环（升级/退化/修复） |
| 1.1.0 | 2026-03 | Phase 2 完成：经验提取（模式 → LLM → SKILL.md） |
| 1.0.0 | 2026-03 | Phase 1 完成：Docker 沙箱 + 轨迹 + 模式 + MCP |

---

## License

MIT © 2026 appcom2016
