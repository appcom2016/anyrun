# anyrun

> AI Agent 的 Docker 沙箱执行引擎 — 自带全量观测、模式发现、经验提取和自进化。
> 零配置，一行代码在隔离容器中安全运行任意 Python。

## 安装

```bash
pip install anyrun-agent
```

要求：Python 3.10+，Docker 运行中。

## 核心能力

```
Agent 执行代码 → anyrun Docker 沙箱
                    ↓
              自动采集执行轨迹（SQLite + JSON）
                    ↓
              模式发现（错误聚类 / 成功路径 / 异常检测）
                    ↓
              经验提取（LLM 从模式中提炼 SKILL.md）
                    ↓
              自进化（beta→prod→decayed→retired + 自动修复）
```

## 快速开始

### Sandbox：安全执行代码

```python
from anyrun import Sandbox

# 零配置，一行代码
with Sandbox() as s:
    result = s.run("print(1 + 1)")
    print(result.data)  # "2\n"
```

### 更多 Sandbox 用法

```python
# 命名工作区
sandbox = Sandbox(host_workspace_root="./my_workspace")

# 多行代码
result = sandbox.run("""
import sys
print(f"Python {sys.version}")
x = sum(range(1000))
print(f"Sum: {x}")
""")

# 错误处理
result = sandbox.run("1 / 0")
print(result.success)  # False
print(result.error)    # ZeroDivisionError: division by zero

# 文件操作（会话内持久化）
sandbox.run('open("/app/workspace/data.txt", "w").write("hello")')
result = sandbox.run('print(open("/app/workspace/data.txt").read())')
print(result.data)  # "hello\n"

# 多会话隔离
sandbox.run("x = 42", session_id="session-a")
sandbox.run("y = 100", session_id="session-b")
sandbox.cleanup_session("session-a")

# 关联 skill 用于自进化追踪
sandbox.run("print(42)", skill_name="my-skill")
```

### ToolRegistry：管理工具和技能

```python
from anyrun import ToolRegistry, Tool

registry = ToolRegistry()

# 查看内置工具
tools = registry.get_tools_info()
# [{"name": "shell", ...}, {"name": "create_file", ...}]

# 添加自定义工具
registry.add_tool(Tool(
    name="add_numbers",
    description="对两个数字求和",
    parameters={"a": {"type": "integer"}, "b": {"type": "integer"}},
    code="def execute_tool(a: int, b: int):\n    return a + b",
))

# 获取工具代码（供 Sandbox 执行）
tool = registry.get_tool("shell")
```

### Agent 集成示例

```python
from anyrun import Sandbox, ToolRegistry

registry = ToolRegistry()
sandbox = Sandbox()

# 将 anyrun 工具转为 OpenAI function calling 格式
def to_openai_tool(tool_info):
    return {
        "type": "function",
        "function": {
            "name": tool_info["name"],
            "description": tool_info["description"],
            "parameters": {"type": "object", "properties": {
                k: v if isinstance(v, dict) else {"type": v}
                for k, v in tool_info["parameters"].items()
            }},
        },
    }

tools = [to_openai_tool(t) for t in registry.get_tools_info()]

# 喂给 DeepSeek/GPT 等模型
# response = client.chat.completions.create(model="deepseek-v4-flash", tools=tools, ...)

# 模型返回 tool_call 后在 Sandbox 中执行
result = sandbox.execute_tool(ToolExecutionRequest(
    tool_code=tool.code,
    parameters=json.loads(tool_call.arguments),
    session_id="agent-001",
    tool_name=tool.name,
))
```

## CLI

### 执行轨迹

```bash
# 查看最近 20 条
anyrun traces ls

# 只看失败的
anyrun traces ls --errors

# 查看详情
anyrun traces show <trace_id>

# 统计
anyrun traces stats
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
# 手动分析
anyrun patterns analyze

# 查看已发现的模式
anyrun patterns ls

# 查看详情
anyrun patterns show <pattern_id>
```

### 经验提取

```bash
# 从所有活跃模式中提取
anyrun extract

# 从指定模式提取
anyrun extract --pattern-id <id>

# 查看生成的技能
ls ~/.anyrun/skills/
```

生成的 SKILL.md 示例（`python-int-safe-conversion/SKILL.md`）：
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

## 常见陷阱
- isdigit() 不能处理负数
- 注意空白字符（先 .strip()）
- 同时捕获 ValueError 和 TypeError
```

### 自进化

```bash
# 查看技能健康状态
anyrun evolution stats

# 修复退化的技能
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

## MCP Server

在 Hermes 或 Claude Desktop 中配置：

```yaml
mcp_servers:
  anyrun:
    command: "python3"
    args: ["-m", "anyrun.mcp_server"]
```

暴露工具：
- `sandbox_run` — Docker 沙箱执行代码
- `trace_list` — 列出执行轨迹
- `trace_get` — 获取单条轨迹详情
- `trace_stats` — 执行统计

## API 参考

### Sandbox

```python
Sandbox(
    host_workspace_root=None,   # None=自动临时目录
    docker_image="python:3.12-slim",
)
```

| 方法 | 说明 |
|------|------|
| `run(code, session_id, timeout, skill_name)` | 执行 Python 代码 → ExecutionResult |
| `execute_tool(request)` | 执行注册的工具 |
| `cleanup_session(session_id)` | 清理会话容器 |
| 支持 `with Sandbox() as s:` | 退出自动清理 |

### ExecutionResult

```python
@dataclass
class ExecutionResult:
    success: bool          # 是否成功
    data: Any              # stdout 输出
    error: str | None      # 错误信息
    logs: dict | None      # stdout/stderr/traceback/error_type
    metadata: dict | None  # execution_time, container_id, session_id
```

### ToolRegistry

| 方法 | 说明 |
|------|------|
| `get_tools_info()` | 获取所有工具摘要（LLM 格式） |
| `get_tool(name)` | 获取单个工具 |
| `add_tool(tool)` | 添加工具 |
| `update_tool_code(name, code)` | 更新工具代码 |
| `promote_tool(name)` | beta → prod |
| `delete_tool(name)` | 删除工具 |
| `get_skills_info()` | 获取已加载技能列表 |

## 数据存储

```
~/.anyrun/
├── traces/
│   ├── data/          # 执行轨迹 JSON
│   ├── index.db       # SQLite 索引
│   └── patterns/      # 发现的模式
├── skills/            # 自动提取的技能
│   └── <name>/SKILL.md
└── evolution/
    └── evolution.db   # 技能生命周期数据
```

## 路线图

- [x] Phase 1: Docker 沙箱 + 轨迹 + 模式 + MCP + CLI
- [x] Phase 2: 经验提取（模式 → LLM → SKILL.md）
- [x] Phase 3: 自进化闭环（升级/退化/修复）
- [ ] Phase 4: 集体学习网络

## License

MIT
