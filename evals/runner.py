"""运行器：同一个任务跑 N 次，收集可复现的结果。

三次跑出三个结论的评测系统等于没评测。所以每次运行都要留下：
  - 初始状态哈希（证明 N 次的环境真的一样）
  - 每一步的 trajectory（证明结论从哪来）
  - 每条 check 的明细（证明失败在哪一环）
"""
from __future__ import annotations

import platform
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from .agents import Agent, ToolBox, Trajectory
from .checks import CheckResult, protected_violations, run_checks
from .sandbox import Sandbox
from .spec import Task


@dataclass
class RunResult:
    task_id: str
    repeat: int
    passed: bool
    checks: list[CheckResult]
    violations: list[CheckResult]
    steps: int
    finished: bool
    wall_time: float
    state_hash_before: str
    state_hash_after: str
    trajectory: Trajectory | None = None
    error: str = ""

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]

    @property
    def all_failures(self) -> list[CheckResult]:
        return self.failed_checks + [v for v in self.violations if not v.passed]

    def failure_reason(self) -> str:
        if self.error:
            return f"异常: {self.error}"
        if not self.finished:
            return "没调 finish（步数用完 / 死循环）"
        fails = self.all_failures
        if not fails:
            return ""
        return "; ".join(f"{c.kind}:{c.target}" for c in fails[:3])


def run_once(task: Task, agent: Agent, sandbox_factory: Callable[[], Sandbox],
             keep: bool = False) -> RunResult:
    sb = sandbox_factory()
    try:
        # 1) 干净环境 + 初始状态
        sb.reset(task.files)
        baseline = {p: sb.file_hash(p) for p in task.protected}
        hash_before = sb.state_hash()

        # 2) 让 agent 干活（它只拿到 ToolBox）
        toolbox = ToolBox(sb, timeout=task.limits.timeout_sec)
        t0 = time.perf_counter()
        traj = Trajectory()
        error = ""
        try:
            traj = agent.solve(task, toolbox)
        except Exception as e:  # noqa: BLE001 —— agent 崩了要记成一次失败运行，不能中断整轮
            error = f"{type(e).__name__}: {e}"
        wall = time.perf_counter() - t0

        # 3) 只看最终状态判分（agent 够不着的地方）
        checks = run_checks(task.checks, sb, baseline)
        violations = protected_violations(sb, task.protected, baseline)
        hash_after = sb.state_hash()

        passed = (not error) and all(c.passed for c in checks) and all(v.passed for v in violations)

        return RunResult(
            task_id=task.id, repeat=0, passed=passed, checks=checks, violations=violations,
            steps=traj.n_steps, finished=traj.finished, wall_time=wall,
            state_hash_before=hash_before, state_hash_after=hash_after,
            trajectory=traj, error=error,
        )
    finally:
        if not keep:
            sb.close()


def run_task(task: Task, agent: Agent, repeats: int = 3,
             sandbox_factory: Callable[[], Sandbox] | None = None,
             keep: bool = False) -> list[RunResult]:
    factory = sandbox_factory or (lambda: __import__("evals").LocalSandbox())
    out: list[RunResult] = []
    for i in range(1, repeats + 1):
        r = run_once(task, agent, factory, keep=keep)
        r.repeat = i
        out.append(r)
    return out


@dataclass
class SuiteResult:
    agent_name: str
    results: list[RunResult] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    started_at: str = ""
    ended_at: str = ""
    environment: dict[str, str] = field(default_factory=dict)

    def task_ids(self) -> list[str]:
        seen, out = set(), []
        for t in self.tasks:
            if t.id not in seen:
                seen.add(t.id)
                out.append(t.id)
        return out

    def for_task(self, task_id: str) -> list[RunResult]:
        return [r for r in self.results if r.task_id == task_id]


def run_suite(tasks: list[Task], agent: Agent, repeats: int = 3,
              sandbox_factory: Callable[[], Sandbox] | None = None,
              on_progress: Callable[[str], None] | None = None,
              keep: bool = False) -> SuiteResult:
    factory = sandbox_factory or (lambda: __import__("evals").LocalSandbox())
    suite = SuiteResult(
        agent_name=agent.name,
        tasks=list(tasks),
        started_at=datetime.now().isoformat(timespec="seconds"),
        environment={
            "python": platform.python_version(),
            "platform": platform.platform(),
            "sandbox": type(factory()).__name__,
            "repeats": str(repeats),
        },
    )
    for task in tasks:
        for i in range(1, repeats + 1):
            if on_progress:
                on_progress(f"  {task.id} 第 {i}/{repeats} 次")
            r = run_once(task, agent, factory, keep=keep)
            r.repeat = i
            suite.results.append(r)
    suite.ended_at = datetime.now().isoformat(timespec="seconds")
    return suite


def run_matrix(tasks: list[Task], agents: list[Agent], repeats: int = 3,
               sandbox_factory: Callable[[], Sandbox] | None = None,
               on_progress: Callable[[str], None] | None = None) -> dict[str, SuiteResult]:
    """多个 agent 跑同一批任务 —— 这才是评测系统的正经用法：
    同一套题、同一个环境，只换模型。"""
    out: dict[str, SuiteResult] = {}
    for agent in agents:
        if on_progress:
            on_progress(f"[{agent.name}]")
        out[agent.name] = run_suite(tasks, agent, repeats=repeats,
                                    sandbox_factory=sandbox_factory,
                                    on_progress=on_progress)
    return out
