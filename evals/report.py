"""报告：markdown 输出。

一份能拿给面试官看的评测报告，必须自带三样东西：
  1. 数字（带置信区间，不是裸的百分数）
  2. 归因（失败在哪一条 check，不是"效果不好"）
  3. 可复现证据（环境哈希一致、参数齐全）
"""
from __future__ import annotations

from .stats import (environment_consistency, failure_taxonomy, min_sample_warning,
                    summarize, summarize_by_category, summarize_by_task, tool_histogram)


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def render_markdown(suite, title: str | None = None) -> str:
    s = summarize(suite.results)
    by_task = summarize_by_task(suite)
    by_cat = summarize_by_category(suite)
    cat_of = {t.id: t.category for t in suite.tasks}
    diff_of = {t.id: t.difficulty for t in suite.tasks}
    name_of = {t.id: t.name for t in suite.tasks}

    L: list[str] = []
    L.append(f"# {title or 'Agent 评测报告'}")
    L.append("")
    L.append(f"- **Agent**：`{suite.agent_name}`")
    L.append(f"- **任务数**：{len(suite.task_ids())}　**每任务重复**：{suite.environment.get('repeats', '?')}　**总运行**：{s.n}")
    L.append(f"- **时间**：{suite.started_at} → {suite.ended_at}")
    L.append(f"- **环境**：Python {suite.environment.get('python')} / {suite.environment.get('sandbox')}")
    L.append(f"- **平台**：{suite.environment.get('platform')}")
    L.append("")

    L.append(f"## 整体：**{_pct(s.rate)}**（{s.passed}/{s.n}，95% CI {s.ci_text()}）")
    L.append("")
    warn = min_sample_warning(s.n)
    if warn:
        L.append(f"> {warn}")
        L.append("")

    L.append("## 逐任务")
    L.append("")
    L.append("| 任务 | 类别 | 难度 | 通过 | 成功率 | 95% CI | 稳定性 | 平均步数 | 平均耗时 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for tid in suite.task_ids():
        t = by_task[tid]
        L.append("| `{}` {} | {} | {} | {}/{} | {} | {} | {} | {:.1f} | {:.2f}s |".format(
            tid, name_of.get(tid, ""), cat_of.get(tid, "?"), diff_of.get(tid, "?"),
            t.passed, t.n, _pct(t.rate), t.ci_text(), t.stability,
            t.avg_steps, t.avg_time))
    L.append("")

    if len(by_cat) > 1:
        L.append("## 分类别")
        L.append("")
        L.append("| 类别 | 通过 | 成功率 | 95% CI |")
        L.append("|---|---|---|---|")
        for cat, t in by_cat.items():
            L.append(f"| {cat} | {t.passed}/{t.n} | {_pct(t.rate)} | {t.ci_text()} |")
        L.append("")

    tax = failure_taxonomy(suite)
    L.append("## 失败归因")
    L.append("")
    if not tax:
        L.append("全部通过，没有失败。")
    else:
        L.append("| 失败在哪一环 | 次数 |")
        L.append("|---|---|")
        for k, v in tax.items():
            L.append(f"| {k} | {v} |")
    L.append("")

    cons = environment_consistency(suite)
    L.append("## 可复现性证据")
    L.append("")
    L.append("同一任务的初始状态哈希必须在 N 次重复之间完全一致，否则环境本身就在变：")
    L.append("")
    L.append("| 任务 | 初始哈希 | 结论 |")
    L.append("|---|---|---|")
    for tid, hashes in cons.items():
        joined = " / ".join(sorted(hashes))
        ok = "✅ 一致" if len(hashes) == 1 else "❌ 不一致，结果不可比"
        L.append(f"| `{tid}` | {joined} | {ok} |")
    L.append("")

    hist = tool_histogram(suite)
    if hist:
        L.append("## Agent 行为")
        L.append("")
        L.append("| 工具 | 调用次数 |")
        L.append("|---|---|")
        for k, v in hist.items():
            L.append(f"| `{k}` | {v} |")
        L.append("")

    flaky = [tid for tid, t in by_task.items() if t.flaky]
    if flaky:
        L.append("## ⚠ 抖动任务")
        L.append("")
        L.append("有时过有时不过 —— 这类结果最不能当结论用，先查清楚原因再谈优化：")
        L.append("")
        for tid in flaky:
            L.append(f"- `{tid}`：{by_task[tid].passed}/{by_task[tid].n}")
        L.append("")

    return "\n".join(L)


def render_comparison(suites: dict, tasks: list) -> str:
    """多 agent 对比：同一套题、同一个沙箱，只换模型。"""
    L: list[str] = ["# Agent 横向对比", ""]
    L.append("| Agent | 通过 | 成功率 | 95% CI | 平均步数 | 抖动任务数 |")
    L.append("|---|---|---|---|---|---|")
    for name, suite in suites.items():
        s = summarize(suite.results)
        from .stats import summarize_by_task
        flaky = sum(1 for t in summarize_by_task(suite).values() if t.flaky)
        L.append(f"| `{name}` | {s.passed}/{s.n} | {_pct(s.rate)} | {s.ci_text()} | "
                 f"{s.avg_steps:.1f} | {flaky} |")
    L.append("")
    all_n = sum(len(s.results) for s in suites.values())
    warn = min_sample_warning(all_n)
    if warn:
        L.append(f"> {warn}")
        L.append("")
    return "\n".join(L)
