"""anyrun MCP Server 端到端测试 — 模拟 Hermes 集成"""
import asyncio, sys, json

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters


async def main():
    fail_count = 0
    tests_run = 0

    def check(name, ok, detail=""):
        nonlocal fail_count, tests_run
        tests_run += 1
        status = "PASS" if ok else "FAIL"
        if not ok:
            fail_count += 1
        print(f"  [{status}] {name}" + (f"  → {detail}" if detail else ""))

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "anyrun.mcp_server"],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            # Init
            init = await session.initialize()
            check("Initialize", init.serverInfo.name == "anyrun")

            # List tools
            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            check("tools/list returns 4 tools", len(tools.tools) == 4)
            check("Tools: sandbox_run", "sandbox_run" in tool_names)
            check("Tools: trace_list", "trace_list" in tool_names)
            check("Tools: trace_get", "trace_get" in tool_names)
            check("Tools: trace_stats", "trace_stats" in tool_names)

            # sandbox_run - success
            r = await session.call_tool("sandbox_run", {"code": "print(42)"})
            data = json.loads(r.content[0].text)
            check("sandbox_run: print(42)", data["success"], f"got {data['data']!r}")

            # sandbox_run - syntax error
            r = await session.call_tool("sandbox_run", {"code": "bad syntax"})
            data = json.loads(r.content[0].text)
            check("sandbox_run: syntax error", not data["success"] and "SyntaxError" in data["error"], data["error"][:40])

            # sandbox_run - empty code
            r = await session.call_tool("sandbox_run", {"code": ""})
            data = json.loads(r.content[0].text)
            check("sandbox_run: empty code", not data["success"] and "code is required" in data["error"], data["error"])

            # sandbox_run - runtime error
            r = await session.call_tool("sandbox_run", {"code": "1/0"})
            data = json.loads(r.content[0].text)
            check("sandbox_run: ZeroDivisionError", not data["success"] and "ZeroDivisionError" in data["error"], data["error"][:40])

            # trace_stats
            r = await session.call_tool("trace_stats", {})
            stats = json.loads(r.content[0].text)
            check("trace_stats has total", "total" in stats, f"total={stats['total']}")
            check("trace_stats has success_rate", "success_rate" in stats)
            check("trace_stats has top_errors", "top_errors" in stats)

            # trace_list
            r = await session.call_tool("trace_list", {"limit": 3})
            traces = json.loads(r.content[0].text)
            check("trace_list returns list", isinstance(traces, list), f"len={len(traces)}")

            # trace_get - not found
            r = await session.call_tool("trace_get", {"trace_id": "nonexistent"})
            data = json.loads(r.content[0].text)
            check("trace_get: not found", "not found" in data.get("error", ""), data["error"])

            # unknown tool
            r = await session.call_tool("nonexistent_tool", {})
            check("unknown tool returns error", "unknown tool" in r.content[0].text)

            print(f"\n{'='*40}")
            print(f"Results: {tests_run - fail_count}/{tests_run} passed")
            if fail_count > 0:
                print(f"FAILURES: {fail_count}")
                sys.exit(1)
            else:
                print("All tests PASSED")


asyncio.run(main())
