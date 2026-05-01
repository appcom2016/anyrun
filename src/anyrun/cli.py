"""anyrun CLI — 命令行入口

Usage:
    anyrun [--version]
    anyrun config
    anyrun version
    anyrun session ls
    anyrun session cleanup [--session-id SID] [--delete]
    anyrun traces ls [--errors] [--limit N]
    anyrun traces show <trace_id>
    anyrun traces stats
    anyrun traces cleanup [--max N]
    anyrun patterns ls
    anyrun patterns show <pattern_id>
    anyrun patterns analyze
    anyrun extract [--pattern-id PID]
    anyrun evolution stats
    anyrun evolution repair
"""

import argparse
import sys


def cmd_traces_ls(args):
    from .tracing.collector import get_store

    store = get_store()
    traces = store.list(
        error_only=args.errors,
        limit=args.limit or 20,
    )

    if not traces:
        print("No traces yet. Run some code with Sandbox.run() first.")
        return

    print(f"{'ID':<14} {'Status':<8} {'Duration':<10} {'Session':<14} {'Error'}")
    print("-" * 80)
    for t in traces:
        status = "OK" if t["success"] else "FAIL"
        dur = f"{t['duration_ms']:.0f}ms" if t["duration_ms"] else "-"
        err = t["error_type"] or "-"
        print(f"{t['trace_id']:<14} {status:<8} {dur:<10} {t['session_id']:<14} {err}")


def cmd_traces_show(args):
    from .tracing.collector import get_store

    store = get_store()
    trace = store.get(args.trace_id)

    if trace is None:
        print(f"Trace not found: {args.trace_id}")
        return

    print(f"Trace: {trace.trace_id}")
    print(f"  Session:   {trace.session_id}")
    print(f"  Status:    {'OK' if trace.success else 'FAIL'}")
    print(f"  Duration:  {trace.duration_ms}ms")
    print(f"  Container: {trace.container_id[:12] if trace.container_id else 'N/A'}")
    print(f"  Image:     {trace.container_image}")
    if trace.error_type:
        print(f"  Error:     {trace.error_type}: {trace.error_message}")
        if trace.traceback:
            print(f"  Traceback:\n{trace.traceback}")
    if trace.result_data:
        preview = trace.result_data[:200]
        print(f"  Output:    {preview}")
    print(f"  Code:")
    for line in trace.input_code.strip().split("\n"):
        print(f"    {line}")


def cmd_patterns_ls(args):
    from .tracing.patterns import PatternStore

    store = PatternStore()
    patterns = store.list()

    if not patterns:
        print("No patterns yet. Run `anyrun patterns analyze` or accumulate more traces.")
        return

    for p in patterns:
        icon = {"error_cluster": "❌", "success_path": "✅", "anomaly": "⚠️"}.get(p.type, "•")
        print(f"{icon} [{p.pattern_id}] {p.type}")
        print(f"   {p.description}")
        print(f"   {p.occurrences}次, {p.affected_sessions}会话, status={p.status}")
        print()


def cmd_patterns_show(args):
    from .tracing.patterns import PatternStore

    store = PatternStore()
    p = store.load(args.pattern_id)
    if p is None:
        print(f"Pattern not found: {args.pattern_id}")
        return

    print(f"Pattern: {p.pattern_id}")
    print(f"Type:        {p.type}")
    print(f"Status:      {p.status}")
    print(f"Description: {p.description}")
    print(f"Occurrences: {p.occurrences}")
    print(f"Sessions:    {p.affected_sessions}")
    print(f"First seen:  {p.first_seen}")
    print(f"Last seen:   {p.last_seen}")
    print(f"Samples:     {p.sample_trace_ids}")


def cmd_evolution_stats(args):
    """显示自进化统计"""
    from .evolution import get_tracker
    tracker = get_tracker()
    stats = tracker.stats()

    print(f"Skills tracked: {stats['total']}")
    print(f"  beta:    {stats['beta']}")
    print(f"  prod:    {stats['prod']}")
    print(f"  decayed: {stats['decayed']}")
    print(f"  retired: {stats['retired']}")
    print()

    if stats["skills"]:
        for s in stats["skills"]:
            icon = {"beta": "🔶", "prod": "✅", "decayed": "⚠️", "retired": "💤"}.get(s["status"], "•")
            repair = " [needs repair]" if s.get("needs_repair") else ""
            print(f"  {icon} {s['name']} ({s['status']})")
            print(f"     runs={s['total_runs']}, rate={s['success_rate']}%, sessions={s['sessions']}{repair}")


def cmd_evolution_repair(args):
    """修复退化的 skill"""
    from .evolution import repair_all_decayed
    results = repair_all_decayed()
    print(f"Decayed skills: {results['total']}")
    print(f"  Repaired: {results['repaired']}")
    print(f"  Failed:   {results['failed']}")
    print(f"  Skipped:  {results['skipped']}")


def cmd_extract(args):
    """从模式中自动提取经验"""
    from .tracing.patterns import PatternStore
    from .tracing.extractor import ExperienceExtractor

    pstore = PatternStore()
    patterns = pstore.list()

    active = [p for p in patterns if p.status == "active" and p.occurrences >= 3]
    if not active:
        print("No active patterns with ≥3 occurrences. Run `anyrun patterns analyze` first.")
        return

    if args.pattern_id:
        target = next((p for p in active if p.pattern_id == args.pattern_id), None)
        if target is None:
            print(f"Pattern not found: {args.pattern_id}")
            return
        active = [target]

    extractor = ExperienceExtractor()
    skills = []

    for pattern in active:
        print(f"\n{'='*50}")
        print(f"Extracting from: [{pattern.pattern_id}] {pattern.type}")
        print(f"  {pattern.description}")
        skill = extractor.extract_from_pattern(pattern)
        if skill:
            skills.append(skill)
            print(f"  ✓ Generated: {skill.name}")
            print(f"    Steps: {len(skill.steps)}, Pitfalls: {len(skill.pitfalls)}")

    print(f"\n{'='*50}")
    print(f"Generated {len(skills)} skills → ~/.anyrun/skills/")
    if skills:
        print("Review them with: ls ~/.anyrun/skills/")


def cmd_patterns_analyze(args):
    from .tracing.collector import get_store
    from .tracing.patterns import PatternAnalyzer, PatternStore

    store = get_store()
    analyzer = PatternAnalyzer(store)
    results = analyzer.analyze()

    print(f"Analyzed {results['total_traces']} traces\n")

    print("=== Error Clusters ===")
    for p in results["error_clusters"]:
        print(f"  ❌ {p['description']}")
    if not results["error_clusters"]:
        print("  (none)")

    print("\n=== Success Paths ===")
    for p in results["success_paths"]:
        print(f"  ✅ {p['description']}")
    if not results["success_paths"]:
        print("  (none)")

    print("\n=== Anomalies ===")
    for p in results["anomalies"]:
        print(f"  ⚠️  {p['description']}")
    if not results["anomalies"]:
        print("  (none)")

    # 保存结果
    pstore = PatternStore()
    pstore.clear()
    for p_dict in results["error_clusters"]:
        from .tracing.patterns import Pattern
        pstore.save(Pattern.from_dict(p_dict))
    for p_dict in results["success_paths"]:
        from .tracing.patterns import Pattern
        pstore.save(Pattern.from_dict(p_dict))
    for p_dict in results["anomalies"]:
        from .tracing.patterns import Pattern
        pstore.save(Pattern.from_dict(p_dict))

    print(f"\nSaved to ~/.anyrun/traces/patterns/")


def cmd_traces_stats(args):
    from .tracing.collector import get_store

    store = get_store()
    stats = store.stats()

    print(f"Total traces:     {stats['total']}")
    print(f"Successful:       {stats['success']} ({stats['success_rate']}%)")
    print(f"Failed:           {stats['failed']}")
    print(f"Avg duration:     {stats['avg_duration_ms']}ms")
    print()

    if stats["top_errors"]:
        print("Top errors:")
        for e in stats["top_errors"]:
            print(f"  {e['type']}: {e['count']}x")
        print()

    if stats["recent_sessions"]:
        print("Recent sessions:")
        for s in stats["recent_sessions"]:
            print(f"  {s['session_id']}: {s['traces']} traces")


def cmd_traces_cleanup(args):
    """手动触发 trace 清理"""
    from .tracing.collector import get_store
    store = get_store()
    deleted = store.cleanup(max_traces=args.max_traces)
    print(f"Cleaned up {deleted} old traces (keeping newest {args.max_traces})")


def cmd_config(args):
    """显示 anyrun 配置信息"""
    import pathlib
    print("=== anyrun Configuration ===")
    print(f"Home dir:       {pathlib.Path.home() / '.anyrun'}")
    print(f"Traces dir:     {pathlib.Path.home() / '.anyrun' / 'traces'}")
    print(f"Evolution DB:   {pathlib.Path.home() / '.anyrun' / 'evolution' / 'evolution.db'}")
    print(f"Skills dir:     {pathlib.Path.home() / '.anyrun' / 'skills'}")
    print(f"Toolbox data:   {pathlib.Path.home() / '.anyrun' / 'data' / 'toolbox.json'}")
    print(f"Docker image:   python:3.12-slim")
    print(f"Trace limit:    {getattr(args, 'max_traces', 10000)} (auto-cleanup)")
    print()


def cmd_session_ls(args):
    """列出所有 Docker 会话容器"""
    try:
        from .docker.container import ContainerManager
        mgr = ContainerManager()
        for c in mgr.client.containers.list(all=True, filters={"label": "managed_by=container_manager"}):
            labels = c.labels
            sid = labels.get("session_id", "?")
            status = c.status
            img = c.image.tags[0] if c.image.tags else "?"
            ports = c.ports or {}
            print(f"  {c.short_id}  {status:<10} session={sid:<12} image={img}")
            if ports:
                for host_port, container_ports in ports.items():
                    if container_ports:
                        for mapping in container_ports:
                            print(f"    Port: {mapping.get('HostIp', '0.0.0.0')}:{mapping.get('HostPort', '?')} -> {host_port}")
    except Exception as e:
        print(f"Error listing sessions: {e}")


def cmd_session_cleanup(args):
    """清理 Docker 会话"""
    from .docker.container import ContainerManager
    mgr = ContainerManager()
    count = 0
    for c in mgr.client.containers.list(all=True, filters={"label": "managed_by=container_manager"}):
        try:
            sid = c.labels.get("session_id", "?")
            if args.session_id and sid != args.session_id:
                continue
            if args.delete:
                c.remove(force=True)
                print(f"  Deleted container {c.short_id} (session={sid})")
            else:
                c.stop(timeout=5)
                print(f"  Stopped container {c.short_id} (session={sid})")
            count += 1
        except Exception as e:
            print(f"  Error: {e}")
    if count == 0:
        print("No matching sessions found.")


def cmd_version(args):
    """显示版本信息"""
    from . import __version__
    print(f"anyrun {__version__}")
    print("Self-evolving execution engine for AI Agents")
    print("https://github.com/appcom2016/anyrun")


def main():
    parser = argparse.ArgumentParser(prog="anyrun")
    parser.add_argument("--version", action="store_true", help="Show version")
    sub = parser.add_subparsers(dest="command")

    # config
    sub.add_parser("config", help="Show configuration")

    # version
    sub.add_parser("version", help="Show version")

    # session
    p_sess = sub.add_parser("session", help="Manage Docker sessions")
    p_sess_sub = p_sess.add_subparsers(dest="subcommand")
    p_sess_ls = p_sess_sub.add_parser("ls", help="List active sessions")
    p_sess_clean = p_sess_sub.add_parser("cleanup", help="Stop/delete sessions")
    p_sess_clean.add_argument("--session-id", dest="session_id", default="",
                              help="Only clean specific session")
    p_sess_clean.add_argument("--delete", action="store_true",
                              help="Also delete containers")

    # traces ls
    p_ls = sub.add_parser("traces", help="List execution traces")
    p_ls_sub = p_ls.add_subparsers(dest="subcommand")
    p_list = p_ls_sub.add_parser("ls", help="List traces")
    p_list.add_argument("--errors", action="store_true", help="Only show failed")
    p_list.add_argument("--limit", type=int, default=20)

    p_show = p_ls_sub.add_parser("show", help="Show trace detail")
    p_show.add_argument("trace_id")

    p_stats = p_ls_sub.add_parser("stats", help="Show trace statistics")
    p_clean = p_ls_sub.add_parser("cleanup", help="Clean up old traces")
    p_clean.add_argument("--max", dest="max_traces", type=int, default=10000,
                         help="Max traces to keep (default: 10000)")

    # patterns
    p_pat = sub.add_parser("patterns", help="Pattern discovery")
    p_pat_sub = p_pat.add_subparsers(dest="subcommand")
    p_pat_ls = p_pat_sub.add_parser("ls", help="List patterns")
    p_pat_show = p_pat_sub.add_parser("show", help="Show pattern detail")
    p_pat_show.add_argument("pattern_id")
    p_pat_analyze = p_pat_sub.add_parser("analyze", help="Run pattern analysis")

    # extract
    p_ext = sub.add_parser("extract", help="Extract skills from patterns")
    p_ext.add_argument("--pattern-id", dest="pattern_id", help="Extract from specific pattern")

    # evolution
    p_evo = sub.add_parser("evolution", help="Skill evolution management")
    p_evo_sub = p_evo.add_subparsers(dest="subcommand")
    p_evo_stats = p_evo_sub.add_parser("stats", help="Show evolution statistics")
    p_evo_repair = p_evo_sub.add_parser("repair", help="Repair decayed skills")

    args = parser.parse_args()

    if args.version:
        cmd_version(args)
        return

    if args.command == "traces":
        if args.subcommand == "ls":
            cmd_traces_ls(args)
        elif args.subcommand == "show":
            cmd_traces_show(args)
        elif args.subcommand == "stats":
            cmd_traces_stats(args)
        elif args.subcommand == "cleanup":
            cmd_traces_cleanup(args)
        else:
            parser.print_help()
    elif args.command == "patterns":
        if args.subcommand == "ls":
            cmd_patterns_ls(args)
        elif args.subcommand == "show":
            cmd_patterns_show(args)
        elif args.subcommand == "analyze":
            cmd_patterns_analyze(args)
        else:
            parser.print_help()
    elif args.command == "extract":
        cmd_extract(args)
    elif args.command == "evolution":
        if args.subcommand == "stats":
            cmd_evolution_stats(args)
        elif args.subcommand == "repair":
            cmd_evolution_repair(args)
        else:
            parser.print_help()
    elif args.command == "config":
        cmd_config(args)
    elif args.command == "version":
        cmd_version(args)
    elif args.command == "session":
        if args.subcommand == "ls":
            cmd_session_ls(args)
        elif args.subcommand == "cleanup":
            cmd_session_cleanup(args)
        else:
            parser.print_help()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
