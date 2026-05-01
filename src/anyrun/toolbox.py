"""工具箱 — 工具的增删改查、持久化与 Skills 管理"""

import json
import logging
import os
import threading
from typing import Any, Optional

import yaml

from .models import Tool, Skill


class Toolbox:
    """工具和 Skills 的注册表与管理器。

    职责：
    - 工具：从 JSON 加载/保存，支持 CRUD 和版本管理
    - Skills：从文件系统目录加载 SKILL.md 的 YAML frontmatter
    - 线程安全：所有操作在锁内完成
    """

    def __init__(self, storage_path: str = None, skills_dir: str = None):
        if storage_path is None:
            import pathlib
            pkg_dir = pathlib.Path(__file__).resolve().parent
            pkg_data = pkg_dir / "data" / "toolbox.json"
            storage_path = str(pkg_data)
            # 确保 data/ 目录存在
            pkg_data.parent.mkdir(parents=True, exist_ok=True)
        if skills_dir is None:
            pkg_dir = pathlib.Path(__file__).resolve().parent
            skills_dir = str(pkg_dir.parent / "skills")
        self.storage_path = storage_path
        self.skills_dir = skills_dir
        self.logger = logging.getLogger(__name__)

        self._tools: dict[str, Tool] = {}
        self._skills: dict[str, Skill] = {}
        self._lock = threading.Lock()

        self._load_tools()
        self._load_skills()

    # ── 工具管理 ───────────────────────────────────────────

    def add_tool(self, tool: Tool) -> bool:
        """添加工具。同名覆盖时自动递增版本号。"""
        with self._lock:
            if tool.name in self._tools:
                self.logger.warning(f"工具 '{tool.name}' 已存在，将被覆盖")
                tool.version = self._tools[tool.name].version + 1
            tool.status = "beta"
            self._tools[tool.name] = tool
            self._save_tools()
            self.logger.info(f"工具 '{tool.name}' v{tool.version} (beta) 已添加")
            return True

    def get_tool(self, name: str) -> Optional[Tool]:
        """按名称获取工具（返回副本，防止外部修改）"""
        with self._lock:
            tool = self._tools.get(name)
            return Tool(**tool.to_dict()) if tool else None

    def update_tool_code(self, name: str, new_code: str) -> Optional[Tool]:
        """更新工具代码，重置为 beta 并递增版本"""
        with self._lock:
            tool = self._tools.get(name)
            if tool is None:
                self.logger.error(f"工具 '{name}' 不存在")
                return None
            tool.code = new_code
            tool.status = "beta"
            tool.version += 1
            self._save_tools()
            self.logger.info(f"工具 '{name}' 更新至 v{tool.version} (beta)")
            return Tool(**tool.to_dict())

    def promote_tool(self, name: str) -> bool:
        """将工具从 beta 提升为 prod"""
        with self._lock:
            tool = self._tools.get(name)
            if tool is None or tool.status == "prod":
                return tool is not None
            tool.status = "prod"
            self._save_tools()
            self.logger.info(f"工具 '{name}' 已提升为 prod")
            return True

    def delete_tool(self, name: str) -> bool:
        """删除工具"""
        with self._lock:
            if name not in self._tools:
                return False
            del self._tools[name]
            self._save_tools()
            self.logger.info(f"工具 '{name}' 已删除")
            return True

    def get_tools_info(self) -> list[dict]:
        """获取所有工具的摘要信息，供 Agent (LLM) 使用"""
        with self._lock:
            return [
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                    "status": t.status,
                    "version": t.version,
                }
                for t in self._tools.values()
            ]

    def get_tool_count(self) -> int:
        """工具总数"""
        with self._lock:
            return len(self._tools)

    # ── Skills 管理 ────────────────────────────────────────

    def get_skill(self, name: str) -> Optional[Skill]:
        """获取技能元数据"""
        with self._lock:
            return self._skills.get(name)

    def get_skills_info(self) -> list[dict]:
        """获取所有技能的摘要列表，供 Agent 使用"""
        with self._lock:
            return [
                {"name": s.name, "description": s.description, "path": s.path}
                for s in self._skills.values()
            ]

    def get_skills_prompt(self) -> str:
        """以 LLM 友好的格式返回所有技能信息"""
        with self._lock:
            lines = []
            for skill in self._skills.values():
                lines.append(f"- {skill.name}: {skill.description}")
            return "\n".join(lines)

    # ── 内部：持久化 ───────────────────────────────────────

    def _load_tools(self):
        """从 JSON 文件加载工具，如果不存在则初始化默认工具"""
        with self._lock:
            if not os.path.exists(self.storage_path):
                self.logger.info(f"工具存储文件不存在: {self.storage_path}")
                self._init_default_tools()
                return
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for name, raw in data.items():
                    self._tools[name] = Tool(**raw)
                self.logger.info(f"已加载 {len(self._tools)} 个工具")
            except Exception as e:
                self.logger.error(f"加载工具失败: {e}")
                self._init_default_tools()

    def _init_default_tools(self):
        """初始化默认工具集（基础设施工具 + 文件管理工具）"""
        self._tools["shell"] = Tool(
            name="shell",
            description="在 Docker 沙箱中执行 shell 命令",
            parameters={
                "command": {"type": "string", "description": "要执行的 shell 命令"},
                "timeout": {"type": "integer", "description": "超时秒数，默认 30"},
            },
            code=(
                "import subprocess\n"
                "def execute_tool(command: str, timeout: int = 30):\n"
                "    try:\n"
                "        r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout, encoding='utf-8', errors='replace')\n"
                "        return r.stdout if r.returncode == 0 else f\"错误: {r.stderr}\\n返回码: {r.returncode}\"\n"
                "    except subprocess.TimeoutExpired:\n        return \"错误: 命令超时\"\n"
                "    except Exception as e:\n        return f\"错误: {str(e)}\"\n"
            ),
            status="prod",
            version=1,
        )
        self._tools["create_file"] = Tool(
            name="create_file",
            description="在沙箱工作区中创建、覆盖或追加文本文件",
            parameters={
                "filename": {"type": "string", "required": True, "description": "文件名"},
                "directory": {"type": "string", "required": False, "default": "./", "description": "存放目录"},
                "content": {"type": "string", "required": True, "description": "文件内容"},
                "mode": {"type": "string", "required": False, "default": "write", "enum": ["write", "append"], "description": "写入模式"},
            },
            code=(
                "import os\n"
                "def execute_tool(filename: str, content: str, directory: str = '/app/workspace', mode: str = 'write', encoding: str = 'utf-8'):\n"
                "    os.makedirs(directory, exist_ok=True)\n"
                "    file_path = os.path.join(directory, filename)\n"
                '    write_mode = "w" if mode == "write" else "a"\n'
                "    with open(file_path, write_mode, encoding=encoding) as f:\n"
                "        f.write(content)\n"
                "    return file_path\n"
            ),
            status="prod",
            version=1,
        )
        self._tools["file_read"] = Tool(
            name="file_read",
            description="读取沙箱工作区中的文件内容",
            parameters={
                "path": {"type": "string", "required": True, "description": "文件绝对路径"},
                "offset": {"type": "integer", "default": 1, "description": "起始行号（1-indexed）"},
                "limit": {"type": "integer", "default": 500, "description": "最多读取行数"},
            },
            code=(
                "def execute_tool(path: str, offset: int = 1, limit: int = 500):\n"
                "    import os\n"
                "    if not os.path.exists(path):\n"
                '        return f"错误: 文件不存在 {path}"\n'
                "    with open(path, 'r', encoding='utf-8', errors='replace') as f:\n"
                "        lines = f.readlines()\n"
                "    total = len(lines)\n"
                "    start = max(0, offset - 1)\n"
                "    end = min(total, start + limit)\n"
                "    selected = lines[start:end]\n"
                "    result = ''.join(selected)\n"
                '    return f"[{start+1}-{end}/{total}]\\n{result}"\n'
            ),
            status="prod",
            version=1,
        )
        self._tools["dir_list"] = Tool(
            name="dir_list",
            description="列出沙箱工作区中指定目录的内容（文件和子目录）",
            parameters={
                "path": {"type": "string", "required": True, "description": "目录绝对路径，默认 /app/workspace"},
            },
            code=(
                "import os\n"
                "def execute_tool(path: str = '/app/workspace'):\n"
                "    if not os.path.exists(path):\n"
                '        return f"错误: 目录不存在 {path}"\n'
                "    if not os.path.isdir(path):\n"
                '        return f"错误: {path} 不是目录"\n'
                "    items = []\n"
                "    for name in sorted(os.listdir(path)):\n"
                "        full = os.path.join(path, name)\n"
                "        if os.path.isdir(full):\n"
                '            items.append(f"d {name}/")\n'
                "        elif os.path.islink(full):\n"
                '            items.append(f"l {name} -> {os.readlink(full)}")\n'
                "        else:\n"
                "            size = os.path.getsize(full)\n"
                '            items.append(f"- {name} ({size}B)")\n'
                '    return "\\n".join(items) if items else "(空目录)"\n'
            ),
            status="prod",
            version=1,
        )
        self._tools["file_search"] = Tool(
            name="file_search",
            description="在沙箱工作区中搜索文件内容",
            parameters={
                "pattern": {"type": "string", "required": True, "description": "搜索模式（Python re 语法）"},
                "path": {"type": "string", "default": "/app/workspace", "description": "搜索起始目录"},
                "file_glob": {"type": "string", "default": "", "description": "文件 glob 过滤，如 *.py"},
            },
            code=(
                "import os, re\n"
                "def execute_tool(pattern: str, path: str = '/app/workspace', file_glob: str = ''):\n"
                "    matches = []\n"
                "    for root, dirs, files in os.walk(path):\n"
                "        for fname in sorted(files):\n"
                "            if file_glob:\n"
                "                import fnmatch\n"
                "                if not fnmatch.fnmatch(fname, file_glob):\n"
                "                    continue\n"
                "            fpath = os.path.join(root, fname)\n"
                "            try:\n"
                "                with open(fpath, 'r', encoding='utf-8', errors='replace') as f:\n"
                "                    for lineno, line in enumerate(f, 1):\n"
                "                        if re.search(pattern, line):\n"
                "                            rel = os.path.relpath(fpath, path)\n"
                "                            matches.append(f'{rel}:{lineno}: {line.rstrip()[:120]}')\n"
                "            except Exception:\n"
                "                pass\n"
                '    return "\\n".join(matches[:200]) if matches else "(无匹配)"\n'
            ),
            status="beta",
            version=1,
        )
        self._tools["ensure_dirs"] = Tool(
            name="ensure_dirs",
            description="在沙箱工作区中创建目录，多个目录用逗号分隔",
            parameters={
                "dirs": {"type": "string", "required": True, "description": "要创建的目录路径，多个用逗号分隔"},
            },
            code=(
                "import os\n"
                "def execute_tool(dirs: str):\n"
                "    for d in dirs.split(','):\n"
                "        os.makedirs(d.strip(), exist_ok=True)\n"
                '    return f"created: {dirs}"\n'
            ),
            status="beta",
            version=1,
        )
        self._save_tools()
        self.logger.info("已初始化默认工具集 (shell, create_file)")

    def _save_tools(self):
        """持久化工具到 JSON（必须在锁内调用）"""
        try:
            os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(
                    {name: t.to_dict() for name, t in self._tools.items()},
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
        except Exception as e:
            self.logger.error(f"保存工具失败: {e}")

    def _load_skills(self):
        """从 skills 目录加载 SKILL.md 的 frontmatter"""
        with self._lock:
            if not os.path.exists(self.skills_dir):
                os.makedirs(self.skills_dir, exist_ok=True)
                return

            for item in os.listdir(self.skills_dir):
                skill_dir = os.path.join(self.skills_dir, item)
                if not os.path.isdir(skill_dir):
                    continue
                skill_file = os.path.join(skill_dir, "SKILL.md")
                if not os.path.exists(skill_file):
                    continue
                try:
                    with open(skill_file, "r", encoding="utf-8") as f:
                        content = f.read()
                    if content.startswith("---"):
                        parts = content.split("---", 2)
                        if len(parts) >= 3:
                            meta = yaml.safe_load(parts[1].strip())
                            if isinstance(meta, dict):
                                skill = Skill(
                                    name=meta.get("name", item),
                                    description=meta.get("description", ""),
                                    path=os.path.relpath(skill_dir, self.skills_dir),
                                )
                                self._skills[skill.name] = skill
                                self.logger.info(f"Skill '{skill.name}' 已加载")
                            else:
                                self.logger.debug(f"SKILL.md '{item}' frontmatter 不是 dict")
                        else:
                            self.logger.debug(f"SKILL.md '{item}' frontmatter 格式不完整")
                    else:
                        self.logger.debug(f"SKILL.md '{item}' 缺少 --- frontmatter")
                except Exception as e:
                    self.logger.error(f"加载 Skill '{item}' 失败: {e}")
