#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""agent-eval 命令行入口。

跑真模型（花钱，但便宜）：
    python run_eval.py --env-file ../qq-clone/.env --repeats 1
    python run_eval.py --model deepseek-chat --repeats 5      # 样本多一点才有说服力

不花钱的用法：
    python run_eval.py --agent scripted:perfect   # 用一个"完美"脚本 agent 验证链路
    python run_eval.py --task 04_anti_cheat       # 只跑某个任务

key 永远不会被打印出来。--env-file 只是把 KEY=VALUE 读进环境变量，
已经存在的环境变量优先，不会被文件覆盖。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from evals import (DockerSandbox, LLMAgent, LocalSandbox, ScriptedAgent,  # noqa: E402
                   discover_tasks, render_markdown, run_suite, summarize)
from evals.stats import min_sample_warning  # noqa: E402


def load_env_file(path: Path, quiet: bool = False) -> list[str]:
    """极简 .env 解析。返回读到的变量名列表（不含值）。"""
    names: list[str] = []
    if not path.exists():
        raise FileNotFoundError(f"找不到 {path}")
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if not k:
            continue
        names.append(k)
        os.environ.setdefault(k, v)   # 已有环境变量优先
    if not quiet:
        print(f"已从 {path} 读取 {len(names)} 个变量（不打印值）: {', '.join(names)}")
    return names


def build_agent(spec: str, args) -> object:
    if spec.startswith("scripted:"):
        name = spec.split(":", 1)[1]
        # 复用自检里的脚本，方便零成本验证链路
        from selftest import CHEATER_AGENT, LAZY, LIAR, PERFECT_AGENT  # noqa: PLC0415
        table = {"perfect": PERFECT_AGENT, "lazy": LAZY, "liar": LIAR, "cheater": CHEATER_AGENT}
        if name not in table:
            raise SystemExit(f"未知脚本 agent: {name}；可选 {sorted(table)}")
        return table[name]
    if spec == "llm":
        return LLMAgent(model=args.model, base_url=args.base_url,
                        api_key_env=args.api_key_env, temperature=args.temperature)
    raise SystemExit(f"未知 --agent: {spec}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Agent 评测沙箱")
    ap.add_argument("--agent", default="llm",
                    help="llm | scripted:perfect|lazy|liar|cheater")
    ap.add_argument("--model", default="deepseek-chat")
    ap.add_argument("--base-url", default="https://api.deepseek.com/v1")
    ap.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--repeats", type=int, default=1,
                    help="每个任务重复几次。想下结论就跑 5 次以上。")
    ap.add_argument("--task", action="append", default=None,
                    help="只跑指定 task id，可重复传")
    ap.add_argument("--env-file", default=None, help="从文件读取 API key 等变量")
    ap.add_argument("--sandbox", default="local", choices=["local", "docker"])
    ap.add_argument("--out", default=None, help="报告输出路径")
    args = ap.parse_args()

    if args.env_file:
        load_env_file(Path(args.env_file))

    tasks = discover_tasks(ROOT / "tasks")
    if args.task:
        wanted = set(args.task)
        tasks = [t for t in tasks if t.id in wanted]
        if not tasks:
            raise SystemExit(f"没有匹配的任务: {sorted(wanted)}")

    if args.sandbox == "docker":
        factory = lambda: DockerSandbox(root=ROOT / "runs")  # noqa: E731
    else:
        factory = lambda: LocalSandbox(root=ROOT / "runs")   # noqa: E731

    agent = build_agent(args.agent, args)
    total_runs = len(tasks) * args.repeats
    print(f"agent={agent.name}  任务={len(tasks)}  重复={args.repeats}  共 {total_runs} 次运行")
    print("-" * 60)

    suite = run_suite(tasks, agent, repeats=args.repeats, sandbox_factory=factory,
                      on_progress=lambda m: print(m, flush=True))

    report = render_markdown(suite, f"评测报告 — {agent.name}")
    out = Path(args.out) if args.out else (ROOT / "reports" / f"eval-{agent.name.replace(':', '_')}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    s = summarize(suite.results)
    print("-" * 60)
    print(f"结果: {s.passed}/{s.n} = {s.rate * 100:.1f}%  (95% CI {s.ci_text()})")
    print(f"平均步数: {s.avg_steps:.1f}   平均耗时: {s.avg_time:.2f}s")
    warn = min_sample_warning(s.n)
    if warn:
        print(warn)
    print(f"报告: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
