"""agent-eval —— 可复现的 Agent 评测沙箱。

设计原则（也是这个项目的门槛所在）：
 1. 隔离：每次跑都在全新工作目录里，上次的残留不影响下次
 2. 判分可信：校验逻辑跑在沙箱**外面**，agent 既看不到也改不到
 3. 复现：初始状态哈希、编码固定、无代理干扰，同一任务多次跑结果可解释
 4. 统计：成功率给置信区间，样本不足不下结论
"""
from .spec import Task, Limits, load_task, discover_tasks
from .sandbox import Sandbox, LocalSandbox, DockerSandbox, ExecResult
from .checks import CheckResult, run_checks
from .agents import Agent, ScriptedAgent, LLMAgent, ToolBox, Step, Trajectory
from .runner import RunResult, run_once, run_task, run_suite, SuiteResult
from .stats import wilson_ci, summarize
from .report import render_markdown

__all__ = [
    "Task", "Limits", "load_task", "discover_tasks",
    "Sandbox", "LocalSandbox", "DockerSandbox", "ExecResult",
    "CheckResult", "run_checks",
    "Agent", "ScriptedAgent", "LLMAgent", "ToolBox", "Step", "Trajectory",
    "RunResult", "run_once", "run_task", "run_suite", "SuiteResult",
    "wilson_ci", "summarize", "render_markdown",
]

__version__ = "0.1.0"
