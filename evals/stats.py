"""统计：别拿 3/5 当结论。

小样本下"成功率 60%"这句话本身没有意义。这里做两件事：
  1. Wilson 区间 —— 小样本下比正态近似靠谱，也不会给出负数下界
  2. 抖动识别 —— 同一个任务有时过有时不过，叫 flaky。
     它比"全挂"更危险：说明结果不稳定，换台机器可能就变了。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """二项比例的 Wilson 置信区间（默认 95%）。"""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


@dataclass
class Summary:
    n: int = 0
    passed: int = 0
    rate: float = 0.0
    ci_low: float = 0.0
    ci_high: float = 0.0
    flaky: bool = False
    avg_steps: float = 0.0
    avg_time: float = 0.0
    errors: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def stability(self) -> str:
        if self.n <= 1:
            return "样本不足"
        if self.passed == self.n:
            return "稳定通过"
        if self.passed == 0:
            return "稳定失败"
        return "抖动 ⚠"

    def ci_text(self) -> str:
        return f"{self.ci_low * 100:.1f}%–{self.ci_high * 100:.1f}%"


def summarize(results: Iterable) -> Summary:
    rs = list(results)
    n = len(rs)
    if n == 0:
        return Summary()
    k = sum(1 for r in rs if r.passed)
    lo, hi = wilson_ci(k, n)
    steps = [r.steps for r in rs]
    times = [r.wall_time for r in rs]
    reasons: list[str] = []
    for r in rs:
        reason = r.failure_reason()
        if reason:
            reasons.append(reason)
    return Summary(
        n=n, passed=k, rate=k / n, ci_low=lo, ci_high=hi,
        flaky=(0 < k < n),
        avg_steps=sum(steps) / n,
        avg_time=sum(times) / n,
        errors=sum(1 for r in rs if r.error),
        failures=reasons,
    )


def summarize_by_task(suite) -> dict[str, Summary]:
    return {tid: summarize(suite.for_task(tid)) for tid in suite.task_ids()}


def summarize_by_category(suite) -> dict[str, Summary]:
    buckets: dict[str, list] = {}
    cat_of = {t.id: t.category for t in suite.tasks}
    for r in suite.results:
        buckets.setdefault(cat_of.get(r.task_id, "?"), []).append(r)
    return {cat: summarize(rs) for cat, rs in sorted(buckets.items())}


def failure_taxonomy(suite) -> dict[str, int]:
    """按失败的那一条 check 归类 —— 只报"失败了"没用，要报"失败在哪一环"。"""
    out: dict[str, int] = {}
    for r in suite.results:
        if r.passed:
            continue
        if r.error:
            key = f"agent 异常: {r.error.split(':')[0]}"
        elif not r.finished:
            key = "未完成（步数耗尽/死循环）"
        else:
            fails = r.all_failures
            key = f"{fails[0].kind}: {fails[0].target}" if fails else "未知"
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def tool_histogram(suite) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in suite.results:
        if not r.trajectory:
            continue
        for tool, cnt in r.trajectory.tool_counts().items():
            out[tool] = out.get(tool, 0) + cnt
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def environment_consistency(suite) -> dict[str, set[str]]:
    """每个任务的初始状态哈希集合。集合大小 > 1 就说明环境不可复现，评测白做。"""
    out: dict[str, set[str]] = {}
    for r in suite.results:
        out.setdefault(r.task_id, set()).add(r.state_hash_before)
    return out


def min_sample_warning(n: int, threshold: int = 30) -> str:
    if n >= threshold:
        return ""
    return (f"⚠ 总样本 {n} 次，置信区间很宽，此时说「A 比 B 好」多半是噪声。"
            f"要下结论建议每任务至少跑 {threshold} 次。")
