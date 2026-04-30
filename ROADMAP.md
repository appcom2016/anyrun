# anyrun 产品路线图

> **当前进度**：Phase 1-3 已完成 ✅ | Phase 4 待开始
> - ✅ Phase 1: Docker 沙箱执行 + 全量轨迹采集 + 模式识别 + MCP Server + CLI
> - ✅ Phase 2: 经验提取（LLM 从模式中提炼 SKILL.md）
> - ✅ Phase 3: 自进化闭环（skill 自动升级/退化/修复）
> - ⬜ Phase 4: 集体学习网络（agent 之间共享经验）

> 从「Docker 沙箱库」到「Agent 进化引擎」的四阶段演进。
> 目标：成为 AI Agent 生态的执行观测层 + 经验提取层 + 集体学习层。

---

## 核心飞轮

```
Agent 执行工具 → anyrun 沙箱执行 + 全量轨迹采集    ✅ Phase 1
                      ↓
              模式识别（跨 session 对比）           ✅ Phase 1
                      ↓
              经验提取（LLM 归纳 + 结构化）         ✅ Phase 2
                      ↓
              Skill 自进化（beta→prod→decayed）    ✅ Phase 3
                      ↓
              注入回 Agent → 下次执行更可靠
                      ↑                    ↓
                      └── 持续监控 + 自动修复 ←┘
                      ↓
              集体学习网络（agent 间共享）           ⬜ Phase 4
```

---

## Phase 1：沙箱执行 + 轨迹观测 + 模式识别 ✅

**模块**：`anyrun.Sandbox`, `anyrun.tracing.*`

### 已交付

**Docker 沙箱执行**
- `Sandbox.run(code)` — 零配置，一行代码在隔离容器中运行 Python
- macOS Docker socket 自动检测
- `with Sandbox() as s:` 上下文管理器
- `execute_tool()` — 执行 ToolRegistry 注册的工具
- `pip install anyrun-agent` 即可用

**执行轨迹自动采集**
- 每次 `Sandbox.run()` 自动生成 `ExecutionTrace`
- SQLite 索引 + JSON 文件持久化（`~/.anyrun/traces/`）
- 记录：代码 hash、耗时、容器 ID、error_type、traceback

**模式识别**
- 错误聚类：按 error_type 分组（≥3 次触发）
- 成功路径：按 code_hash 分组（≥5 次触发）
- 异常检测：基于 duration z-score
- 每 50 条 trace 后台异步自动分析

**CLI + MCP**
- `anyrun traces ls/show/stats` + `anyrun patterns ls/show/analyze`
- MCP Server：`sandbox_run, trace_list, trace_get, trace_stats`

---

## Phase 2：经验提取 ✅

**模块**：`anyrun.tracing.extractor`

### 已交付

**提取 Pipeline**
```
Pattern → 样本 traces → LLM prompt → SKILL.md → 保存到 ~/.anyrun/skills/
```

- `ExperienceExtractor` — 从任一 Pattern 中提取经验
- 智能命名：错误类型识别 → `python-zero-division-guard`, `python-int-safe-conversion`
- 结构化解析：frontmatter + 步骤 + 触发条件 + 常见陷阱
- SKILL.md 按子目录保存（与 Hermes 兼容）

**CLI**
- `anyrun extract` — 批量提取所有活跃模式
- `anyrun extract --pattern-id X` — 单模式提取

**验证结果**
- ZeroDivisionError 7x → `python-zero-division-guard`（2 步骤 + 1 陷阱）
- ValueError 3x → `python-int-safe-conversion`（3 步骤 + 4 陷阱）
- 成功路径 → `success-pattern-*`（3 步骤 + 3 陷阱）

---

## Phase 3：自进化闭环 ✅

**模块**：`anyrun.evolution.*`

### 已交付

**生命周期状态机**
```
auto_extracted → beta ──(20次成功 + 3个session)──→ prod
                         prod ──(最近10次 < 80%)──→ decayed
                         decayed ──(30天无修复)──→ retired
                         decayed ──(修复验证通过)──→ beta (v+1)
```

**EvolutionTracker** — SQLite 持久化
- 记录每次 skill 使用（成功/失败/session/trace_id）
- 滑动窗口统计（最近 10/20 次）
- 自动评估状态变迁

**AutoRepair** — LLM 驱动修复
- 分析退化原因 → 调用 LLM 生成修复 → 沙箱验证 → 保存新版本
- 最多尝试 3 次，验证失败则跳过
- 后处理：自动提取 LLM 输出的 SKILL.md 部分

**CLI**
- `anyrun evolution stats` — 技能健康仪表盘
- `anyrun evolution repair` — 修复退化技能

**Sandbox 集成**
- `s.run(code, skill_name="xxx")` — 自动追踪技能使用
- 与 ExecutionTrace 双向关联（trace_id）

**验证结果**
- beta→prod：28 次成功 + 3 session → 自动升级
- prod→decayed：8 次失败 → 自动降级
- 自动修复：v1→v2，LLM 分析退化原因并生成改进版

---

## Phase 4：集体学习网络（计划中）

**目标**：让 agent 之间共享经验。一个 agent 学到的，所有 agent 受益。

### 4.1 经验市场

anyrun 实例选择将匿名化经验发布到公共经验池：
- 发布：skill 定义 + 性能统计 + 模式签名
- 不发布：用户数据、文件内容、敏感参数

### 4.2 经验发现

```
$ anyrun market search "pip install docker build-essential"
→ 找到 3 个相关经验 (评分 4.8/5, 下载量 1200+)

$ anyrun market install skill/docker-pip-c-extensions
→ 已安装。状态: beta（需本地验证后升 prod）
```

---

## 当前代码结构

```
anyrun/
├── __init__.py              # Sandbox, ToolRegistry 公开 API
├── config.py, models.py     # 配置和数据模型
├── toolbox.py               # 工具注册与管理
├── cli.py                   # CLI 入口
├── mcp_server.py            # MCP Server
├── docker/                  # Docker 沙箱执行
│   ├── executor.py          # Sandbox.run() + execute_tool()
│   ├── container.py         # 容器生命周期管理
│   ├── paths.py             # 路径映射
│   └── async_executor.py    # 异步封装
├── tracing/                 # 轨迹 + 模式 + 提取
│   ├── models.py            # ExecutionTrace
│   ├── store.py             # SQLite + JSON 存储
│   ├── collector.py         # 自动采集 + 自动分析
│   ├── patterns.py          # PatternAnalyzer + PatternStore
│   └── extractor.py         # ExperienceExtractor
└── evolution/               # 自进化
    ├── lifecycle.py         # SkillLifecycle 状态机
    ├── tracker.py           # EvolutionTracker
    ├── repair.py            # AutoRepair
    └── engine.py            # EvolutionEngine
```

---

## 商业模式

### 开源核心（永远免费）
- Phase 1-3 全部功能
- pip install + MCP server + CLI

### Pro（$29/月）
- 优先支持 + 高级统计
- LLM 调用费用按量计费

### Team（$99/月/5 seats）
- 团队内经验共享（私有经验池）

### Enterprise（Phase 4 起）
- 自部署经验市场
- SSO / RBAC / SLA

---

## 竞争分析

| 维度 | Mem0 | VibeKit/aisolate | Ghostbox | anyrun |
|------|------|-----------------|----------|--------|
| 用户记忆 | ✅ | ❌ | ✅ | ❌ |
| 沙箱执行 | ❌ | ✅ | ✅ | ✅ |
| 执行轨迹 | ❌ | 审计日志 | ❌ | ✅ 结构化 |
| 模式发现 | ❌ | ❌ | ❌ | ✅ |
| 经验提炼 | ❌ | ❌ | 提过 | ✅ Phase 2 |
| 自进化 | ❌ | ❌ | ❌ | ✅ Phase 3 |
| 集体学习 | ❌ | ❌ | ❌ | ⬜ Phase 4 |
| 框架无关 | ✅ | Node.js | 平台绑定 | ✅ Python |
| pip install | ✅ | npm | npm | ✅ |

**anyrun 不跟记忆方案竞争，而是互补。**
- Mem0 记住「用户喜欢简洁回答」
- anyrun 记住「Docker python:3.12 里装 psycopg2 需要先 apt-get install libpq-dev」

---

## 风险

### 风险 1：Agent 框架自己做进化
**缓解**：anyrun 定位为独立后端服务，跨框架数据聚合是框架自己做不到的。

### 风险 2：LLM 足够聪明，不需要经验缓存
**缓解**：模型推理 ≠ 一定会在需要时推理。经验提取 = 把推理结果缓存下来，节省 token + 提升可靠性。

### 风险 3：Docker 依赖
**缓解**：后续支持其他沙箱后端（gVisor, Firecracker, WASM）。
