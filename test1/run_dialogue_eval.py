"""对话型评测入口：让真模型去考那 6 道退款题。

跑法：
    py run_dialogue_eval.py --env-file ../qq-clone/.env            # 全部
    py run_dialogue_eval.py --env-file ../qq-clone/.env --task 10_buyer_fault

报告写到 reports/dialogue-<模型>.md。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
from datetime import datetime

HERE = pathlib.Path(__file__).resolve().parent      # test1/
ROOT = HERE.parent                                  # 项目根
sys.path.insert(0, str(ROOT))
TASKS = HERE

from evals.dialogue import LLMDialogueAgent  # noqa: E402
from evals.runner import run_dialogue_suite  # noqa: E402
from evals.spec import discover_tasks  # noqa: E402

REPORTS = ROOT / "reports"


def load_env_file(path: pathlib.Path) -> int:
    """只把变量读进环境，**从不打印值**。"""
    if not path.exists():
        raise FileNotFoundError(f"找不到 env 文件: {path}")
    n = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v:
            os.environ.setdefault(k, v)
            n += 1
    return n


def render(suite, tasks) -> str:
    lines: list[str] = []
    lines.append(f"# 对话型评测报告 — {suite.agent_name}\n")
    lines.append(f"- 开始：{suite.started_at}　结束：{suite.ended_at}")
    lines.append(f"- 任务数：{len(tasks)}　重复：{suite.environment.get('repeats')}")
    lines.append(f"- 沙箱：{suite.environment.get('sandbox')}　"
                 f"Python {suite.environment.get('python')}\n")

    ok = sum(1 for r in suite.results if r.passed)
    lines.append(f"## 总成绩：{ok}/{len(suite.results)}\n")

    for t in tasks:
        for r in suite.for_task(t.id):
            mark = "✅" if r.passed else "❌"
            lines.append(f"### {mark} {t.id}　{r.name if hasattr(r, 'name') else t.name}"
                         f"（第 {r.repeat} 次）\n")
            lines.append(f"- 轮数：{r.turns}　工具调用：{len(r.trajectory.steps) if r.trajectory else 0}"
                         f"　耗时：{r.wall_time:.1f}s")
            if r.error:
                lines.append(f"- ⚠ 异常：{r.error}")

            st = getattr(r.dialogue, "backend_state", {}) or {}
            want = t.dialogue.expect if t.dialogue else {}
            got = st.get("conclusion")
            lines.append(f"- 交的卷：`{got}`")
            lines.append(f"- 期望：`{want}`")
            lines.append(f"- 后端：退款 {len(st.get('refunds') or [])} 条、"
                         f"工单 {len(st.get('tickets') or [])} 条、"
                         f"订单 {st.get('order_status', {}).get(t.dialogue.order_id if t.dialogue else '', '?')}")

            lines.append("\n**逐条 check：**\n")
            for c in r.checks:
                lines.append(f"- `{'PASS' if c.passed else 'FAIL'}`　{c.kind}: {c.target}"
                             + (f"　—　{c.detail}" if c.detail else ""))
            if r.all_failures:
                lines.append(f"\n**失败归因：** `{r.failure_reason()}`\n")

            d = r.dialogue
            if d is not None:
                lines.append("\n**对话记录：**\n")
                lines.append("```")
                lines.append(d.transcript.render())
                lines.append("```")
                lines.append("\n**工具调用序列：**")
                lines.append("```")
                lines.append(" → ".join(d.transcript.tools_called()) or "（一次都没调）")
                lines.append("```")
            lines.append("\n---\n")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="deepseek-chat")
    ap.add_argument("--base-url", default="https://api.deepseek.com/v1")
    ap.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--env-file", default="../../qq-clone/.env")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--task", action="append", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--tasks", default=None,
                    help="题目目录（默认本目录 test1/）；要跑 test2 就写 --tasks test2")
    args = ap.parse_args()

    if args.env_file:
        p = pathlib.Path(args.env_file)
        n = load_env_file(p if p.is_absolute() else HERE / p)
        print(f"[env] 从 {args.env_file} 读入 {n} 个变量（值不打印）")

    tdir = pathlib.Path(args.tasks) if args.tasks else TASKS
    if not tdir.is_absolute():
        tdir = ROOT / tdir
    tasks = discover_tasks(tdir, kind="dialogue")
    if args.task:
        wanted = set(args.task)
        tasks = [t for t in tasks if t.id in wanted]
    if not tasks:
        print("没有匹配的对话型任务")
        return 1

    print(f"[run] 模型={args.model}　任务={len(tasks)}　重复={args.repeats}")
    for t in tasks:
        print(f"      - {t.id}　{t.name}")

    def factory():
        return LLMDialogueAgent(model=args.model, base_url=args.base_url,
                                api_key_env=args.api_key_env,
                                temperature=args.temperature)

    suite = run_dialogue_suite(tasks, factory, repeats=args.repeats,
                               on_progress=lambda s: print(s, flush=True))

    print()
    print("=" * 70)
    for t in tasks:
        for r in suite.for_task(t.id):
            mark = "✅ 过" if r.passed else "❌ 挂"
            st = getattr(r.dialogue, "backend_state", {}) or {}
            print(f"  {mark}  {t.id:<20} 轮数={r.turns}  "
                  f"卷={st.get('conclusion')}")
            if not r.passed:
                print(f"        归因：{r.failure_reason()}")
    ok = sum(1 for r in suite.results if r.passed)
    print("=" * 70)
    print(f"  成绩：{ok}/{len(suite.results)}")

    out = pathlib.Path(args.out) if args.out else (
        REPORTS / f"dialogue-{args.model}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(suite, tasks), encoding="utf-8")
    print(f"  报告：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
