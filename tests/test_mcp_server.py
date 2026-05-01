"""MCP Server 功能覆盖测试（单 session 共享连接）"""
import json, os, sys, pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

MCP_CMD = "/usr/local/bin/python3"
PARAMS = StdioServerParameters(command=MCP_CMD, args=["-m", "anyrun.mcp_server"])
TOOLBOX = os.path.join(os.path.dirname(__file__), "..", "src", "anyrun", "data", "toolbox.json")

_DOCKER_OK = False
try:
    import docker; client = docker.from_env(); client.ping(); _DOCKER_OK = True
except Exception:
    pass

# ── Session-scoped fixture: 启动 MCP 子进程并共享连接 ──

import asyncio
from contextlib import asynccontextmanager


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


class Client:
    def __init__(self, session):
        self._session = session
    async def tools(self):
        r = await self._session.list_tools()
        return {t.name: t for t in r.tools}
    async def call(self, name, args=None):
        if args is None: args = {}
        r = await self._session.call_tool(name, args)
        text = r.content[0].text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"error": f"non-JSON response: {text}"}
    async def ok(self, name, args=None):
        r = await self.call(name, args)
        assert r.get("success", True) is not False, f"{name}: {r}"; return r
    async def err(self, name, args=None):
        r = await self.call(name, args)
        assert "error" in r, f"{name} expect error: {r}"; return r


@pytest.fixture(scope="module")
async def client(event_loop):
    ctx = stdio_client(PARAMS)
    read, write = await ctx.__aenter__()
    sess = await ClientSession(read, write).__aenter__()
    await sess.initialize()
    c = Client(sess)
    yield c
    try:
        await sess.__aexit__(None, None, None)
    except Exception:
        pass
    try:
        await ctx.__aexit__(None, None, None)
    except Exception:
        pass


@pytest.fixture(scope="module")
def backup():
    bak = None
    if os.path.exists(TOOLBOX):
        bak = json.load(open(TOOLBOX, encoding="utf-8"))
    yield
    if bak is not None:
        json.dump(bak, open(TOOLBOX, "w", encoding="utf-8"), indent=2, ensure_ascii=False)


# ══════════════════════════════════════════════════
#  1. Tool Discovery
# ══════════════════════════════════════════════════

EXPECTED = {
    "sandbox_run", "trace_list", "trace_get", "trace_stats",
    "toolbox_add_tool", "toolbox_get_tool", "toolbox_update_tool_code",
    "toolbox_promote_tool", "toolbox_delete_tool", "toolbox_get_tools_info",
    "toolbox_get_tool_count", "toolbox_get_skill", "toolbox_get_skills_info",
    "toolbox_get_skills_prompt",
    "shell", "create_file", "ensure_dirs", "file_read", "dir_list", "file_search",
    "session_list", "session_cleanup",
}

class TestDiscovery:
    async def test_all_present(self, client, backup):
        t = await client.tools()
        for n in EXPECTED: assert n in t, f"缺少 {n}"
    async def test_count(self, client, backup):
        assert len(await client.tools()) == len(EXPECTED)
    async def test_unique(self, client, backup):
        t = await client.tools(); assert len(t) == len(set(t))
    async def test_schemas(self, client, backup):
        for n, tool in (await client.tools()).items():
            s = tool.inputSchema
            assert s.get("type") == "object", n
            assert "properties" in s, n

# ══════════════════════════════════════════════════
#  2. Toolbox 管理
# ══════════════════════════════════════════════════

CODE = 'def execute_tool(text: str): return text.upper()\n'

class TestAddTool:
    async def test_basic(self, client, backup):
        r = await client.ok("toolbox_add_tool", {"name": "ta1", "code": CODE})
        assert r["name"] == "ta1"
        assert "ta1" in await client.tools()
    async def test_dup(self, client, backup):
        await client.ok("toolbox_add_tool", {"name": "ta2", "code": CODE})
        await client.ok("toolbox_add_tool", {"name": "ta2", "code": CODE})
        assert (await client.call("toolbox_get_tool", {"name": "ta2"}))["version"] >= 2
    async def test_no_name(self, client, backup):
        assert "name" in (await client.err("toolbox_add_tool", {"name": "", "code": CODE}))["error"]
    async def test_no_code(self, client, backup):
        await client.err("toolbox_add_tool", {"name": "ta3"})
    async def test_bad_json(self, client, backup):
        e = await client.err("toolbox_add_tool", {"name": "tx", "code": CODE, "parameters": "{{{x"})  # noqa
        assert "JSON" in e.get("error", "")
    async def test_dict_param(self, client, backup):
        assert (await client.ok("toolbox_add_tool", {"name": "ta4", "code": CODE, "parameters": '{"x": {"type": "str"}}'}))["name"] == "ta4"
    async def test_empty_string_param(self, client, backup):
        assert (await client.ok("toolbox_add_tool", {"name": "ta5", "code": CODE, "parameters": ""}))["name"] == "ta5"
    async def test_delete_removes(self, client, backup):
        await client.ok("toolbox_add_tool", {"name": "ta_tr", "code": CODE})
        assert "ta_tr" in await client.tools()
        await client.ok("toolbox_delete_tool", {"name": "ta_tr"})
        assert "ta_tr" not in await client.tools()

class TestGetTool:
    async def test_existing(self, client, backup):
        t = await client.call("toolbox_get_tool", {"name": "shell"})
        for k in ("description", "parameters", "code", "status", "version"):
            assert k in t
    async def test_not_found(self, client, backup):
        await client.err("toolbox_get_tool", {"name": "nope"})
    async def test_empty(self, client, backup):
        await client.err("toolbox_get_tool", {"name": ""})

class TestUpdateTool:
    NEW = 'def execute_tool(x): return x.lower()\n'
    async def test_update(self, client, backup):
        b = await client.call("toolbox_get_tool", {"name": "shell"})
        original_code = b["code"]
        await client.ok("toolbox_update_tool_code", {"name": "shell", "code": self.NEW})
        a = await client.call("toolbox_get_tool", {"name": "shell"})
        assert a["version"] > b["version"]
        assert "lower" in a["code"]
        # 恢复原始代码，避免影响后续测试
        await client.ok("toolbox_update_tool_code", {"name": "shell", "code": original_code})
    async def test_not_found(self, client, backup):
        await client.err("toolbox_update_tool_code", {"name": "nope", "code": self.NEW})
    async def test_no_name(self, client, backup):
        await client.err("toolbox_update_tool_code", {"name": "", "code": self.NEW})
    async def test_no_code(self, client, backup):
        await client.err("toolbox_update_tool_code", {"name": "shell", "code": ""})

class TestPromote:
    async def test_promote(self, client, backup):
        n = "tp1"; await client.ok("toolbox_add_tool", {"name": n, "code": 'def execute_tool(): pass'})
        assert (await client.call("toolbox_get_tool", {"name": n}))["status"] == "beta"
        r = await client.ok("toolbox_promote_tool", {"name": n})
        assert r["status"] == "prod"
        assert (await client.call("toolbox_get_tool", {"name": n}))["status"] == "prod"
    async def test_not_found(self, client, backup):
        await client.err("toolbox_promote_tool", {"name": "nope"})
    async def test_already_prod(self, client, backup):
        assert (await client.ok("toolbox_promote_tool", {"name": "shell"}))["status"] == "prod"

class TestDelete:
    async def test_delete(self, client, backup):
        n = "td1"; await client.ok("toolbox_add_tool", {"name": n, "code": 'def execute_tool(): return 1'})
        assert (await client.ok("toolbox_delete_tool", {"name": n}))["name"] == n
        assert "error" in await client.call("toolbox_get_tool", {"name": n})
    async def test_not_found(self, client, backup):
        await client.err("toolbox_delete_tool", {"name": "nope"})

class TestInfo:
    async def test_fields(self, client, backup):
        info = await client.call("toolbox_get_tools_info", {})
        assert isinstance(info, list) and len(info) >= 2
        for t in info:
            for k in ("name", "description", "parameters", "status", "version"):
                assert k in t
    async def test_reflects_add(self, client, backup):
        c = len(await client.call("toolbox_get_tools_info", {}))
        await client.ok("toolbox_add_tool", {"name": "ti1", "code": 'def f(): pass'})
        assert len(await client.call("toolbox_get_tools_info", {})) == c + 1

class TestCount:
    async def test_type(self, client, backup):
        assert isinstance((await client.call("toolbox_get_tool_count", {}))["count"], int)
    async def test_reflects_add(self, client, backup):
        c = (await client.call("toolbox_get_tool_count", {}))["count"]
        await client.ok("toolbox_add_tool", {"name": "tc1", "code": 'def f(): pass'})
        assert (await client.call("toolbox_get_tool_count", {}))["count"] == c + 1

class TestSkills:
    async def test_not_found(self, client, backup):
        await client.err("toolbox_get_skill", {"name": "nope"})
    async def test_info(self, client, backup):
        assert isinstance(await client.call("toolbox_get_skills_info", {}), list)
    async def test_prompt(self, client, backup):
        r = await client._session.call_tool("toolbox_get_skills_prompt", {})
        assert isinstance(r.content[0].text, str)

# ══════════════════════════════════════════════════
#  3. Sandbox（需 Docker）
# ══════════════════════════════════════════════════

@pytest.mark.skipif(not _DOCKER_OK, reason="Docker 不可用")
class TestSandbox:
    async def test_basic(self, client, backup):
        r = await client.call("sandbox_run", {"code": "print('hi')"})
        assert r["success"] and "hi" in (r.get("data") or "")
    async def test_empty(self, client, backup):
        assert "error" in await client.call("sandbox_run", {"code": ""})
    async def test_error(self, client, backup):
        r = await client.call("sandbox_run", {"code": "def foo(:"})
        assert r.get("success") is False

# ══════════════════════════════════════════════════
#  4. 用户工具执行（需 Docker）
# ══════════════════════════════════════════════════

@pytest.mark.skipif(not _DOCKER_OK, reason="Docker 不可用")
class TestUserTools:
    async def test_shell(self, client, backup):
        r = await client.call("shell", {"command": "echo ok"})
        assert r["success"] and "ok" in (r.get("data") or "")
    async def test_create_file(self, client, backup):
        r = await client.call("create_file", {"filename": "x.txt", "content": "abc", "directory": "/tmp/t"})
        assert r["success"]
        r = await client.call("shell", {"command": "cat /tmp/t/x.txt"})
        assert "abc" in (r.get("data") or "")

# ══════════════════════════════════════════════════
#  5. 边界
# ══════════════════════════════════════════════════

class TestEdge:
    async def test_unknown(self, client, backup):
        assert "error" in await client.call("does_not_exist_42", {})
    async def test_full_lifecycle(self, client, backup):
        n = "t_flc"
        await client.ok("toolbox_add_tool", {"name": n, "description": "x", "parameters": "{}", "code": 'def f(): return 1'})
        t = await client.call("toolbox_get_tool", {"name": n})
        assert t["version"] == 1 and t["status"] == "beta"
        await client.ok("toolbox_update_tool_code", {"name": n, "code": 'def f(): return 2'})
        assert (await client.call("toolbox_get_tool", {"name": n}))["version"] == 2
        await client.ok("toolbox_promote_tool", {"name": n})
        assert (await client.call("toolbox_get_tool", {"name": n}))["status"] == "prod"
        await client.ok("toolbox_delete_tool", {"name": n})
        assert "error" in await client.call("toolbox_get_tool", {"name": n})
