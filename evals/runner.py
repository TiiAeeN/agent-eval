"""运行器：同一个任务跑 N 次，收集可复现的结果。

三次跑出三个结论的评测系统等于没评测。所以每次运行都要留下：
  - 初始状态哈希（证明 N 次的环境真的一样）
  - 每一步的 trajectory（证明结论从哪来）
  - 每条 check 的明细（证明失败在哪一环）

两种跑法：
    工具型  run_once / run_task / run_suite      —— agent.solve(task, toolbox)
    对话型  run_dialogue_task_once / run_dialogue_task / run_dialogue_suite

★ 对话型必须传 **agent 工厂**，不能传现成的 agent 对象。
  ScriptedDialogueAgent 和 ScriptedUser 都带状态（演到第几轮了）。
  复用同一个对象跑第二轮，第二轮会全程哑巴 —— 而且不报错，只是分数默默崩。
  这就是「纸 vs 手机」那道题的结论：**这里构造便宜，就该每次新建。**
"""
from __future__ import annotations

import platform
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from .agents import Agent, ToolBox, Trajectory
from .checks import CheckResult, protected_violations, run_checks
from .dialogue import DialogueAgent, run_dialogue_once
from .memory import MemoryStore
from .sandbox import Sandbox
from .spec import Task
from .users import ScriptedUser


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
    # 对话型才有意义
    turns: int = 0
    dialogue: Any = None
    memory_state: dict[str, Any] = field(default_factory=dict)

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
            return "没主动收尾（没调 finish）"
        fails = self.all_failures
        if not fails:
            return ""
        return "; ".join(f"{c.kind}:{c.target}" for c in fails[:3])


# ==========================================================================
# 工具型
# ==========================================================================

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


# ==========================================================================
# 对话型
# ==========================================================================

def run_dialogue_task_once(task: Task, agent: DialogueAgent, sb: Sandbox,
                           backend: Any) -> RunResult:
    """跑一场对话并判分。sb 已经装好初始文件（含订单资料）。"""
    dspec = task.dialogue
    assert dspec is not None, f"{task.id} 不是对话型任务"

    baseline = {p: sb.file_hash(p) for p in task.protected}
    hash_before = sb.state_hash()

    # 长期记忆：每次运行新建一块（但**块内跨会话**保留）
    memory = MemoryStore(dspec.memory)

    t0 = time.perf_counter()
    dres = run_dialogue_once(
        task, agent,
        backend=backend, sb=sb,
        max_turns=dspec.max_turns,
        memory=memory,
        sessions=dspec.sessions or [dspec.user_script],   # 没配多段就单段
        closing=dspec.closing or None,
    )
    wall = time.perf_counter() - t0

    # 判分：对话记录 + 后端快照，都传给 check
    checks = run_checks(task.checks, sb, baseline, runtime=dres)
    violations = protected_violations(sb, task.protected, baseline)
    hash_after = sb.state_hash()

    passed = (not dres.error) and all(c.passed for c in checks) and all(v.passed for v in violations)

    return RunResult(
        task_id=task.id, repeat=0, passed=passed, checks=checks, violations=violations,
        steps=len(dres.trajectory.steps), finished=dres.finished, wall_time=wall,
        state_hash_before=hash_before, state_hash_after=hash_after,
        trajectory=dres.trajectory, error=dres.error,
        turns=dres.turns, dialogue=dres,
        memory_state=dres.memory_state,
    )


def run_dialogue_task(task: Task, agent_factory: Callable[[], DialogueAgent],
                      backend_factory: Callable[[], Any] | None = None,
                      sandbox_factory: Callable[[], Sandbox] | None = None,
                      repeats: int = 1) -> list[RunResult]:
    """跑 N 次对话。

    ⚠ agent_factory 必须是**工厂**：ScriptedDialogueAgent 带状态，
      复用同一个对象，第二轮开始它会全程哑巴 —— 静默地把成绩搞坏。
    """
    factory = sandbox_factory or (lambda: __import__("evals").LocalSandbox())
    if backend_factory is None:
        from .refund import RefundBackend
        backend_factory = RefundBackend

    out: list[RunResult] = []
    for i in range(1, repeats + 1):
        sb = factory()
        try:
            sb.reset(task.files)                 # ← 订单资料进沙箱
            r = run_dialogue_task_once(task, agent_factory(), sb, backend_factory())
            r.repeat = i
            out.append(r)
        finally:
            sb.close()
    return out


# ==========================================================================
# 整套跑
# ==========================================================================

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


def _env(factory: Callable[[], Sandbox], repeats: int) -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sandbox": type(factory()).__name__,
        "repeats": str(repeats),
    }


def run_suite(tasks: list[Task], agent: Agent, repeats: int = 3,
              sandbox_factory: Callable[[], Sandbox] | None = None,
              on_progress: Callable[[str], None] | None = None,
              keep: bool = False) -> SuiteResult:
    factory = sandbox_factory or (lambda: __import__("evals").LocalSandbox())
    suite = SuiteResult(
        agent_name=agent.name,
        tasks=list(tasks),
        started_at=datetime.now().isoformat(timespec="seconds"),
        environment=_env(factory, repeats),
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


def run_dialogue_suite(tasks: list[Task], agent_factory: Callable[[], DialogueAgent],
                       repeats: int = 1,
                       backend_factory: Callable[[], Any] | None = None,
                       sandbox_factory: Callable[[], Sandbox] | None = None,
                       on_progress: Callable[[str], None] | None = None) -> SuiteResult:
    factory = sandbox_factory or (lambda: __import__("evals").LocalSandbox())
    if backend_factory is None:
        from .refund import RefundBackend
        backend_factory = RefundBackend
    name = getattr(agent_factory(), "name", "dialogue-agent")
    suite = SuiteResult(
        agent_name=name,
        tasks=list(tasks),
        started_at=datetime.now().isoformat(timespec="seconds"),
        environment=_env(factory, repeats),
    )
    for task in tasks:
        for i in range(1, repeats + 1):
            if on_progress:
                on_progress(f"  {task.id} 第 {i}/{repeats} 次")
            sb = factory()
            try:
                sb.reset(task.files)
                r = run_dialogue_task_once(task, agent_factory(), sb, backend_factory())
                r.repeat = i
                suite.results.append(r)
            finally:
                sb.close()
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
