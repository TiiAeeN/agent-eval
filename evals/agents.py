"""Agent 层：接口 + 两种实现。

关键设计：agent 只能通过 ToolBox 碰沙箱。
  - ToolBox 是它**唯一**的动作面，所以「它做过什么」能被完整记录成 trajectory
  - Agent.solve() 拿不到 Sandbox 对象，也就拿不到宿主机文件系统
  - 于是同一个任务可以换不同 agent 反复跑，比的就是 agent 本身

ScriptedAgent —— 确定性、不花钱、不需要 key。
    它的存在意义不是「假 agent」，而是**给自己的评测器做测试**：
    一个只会嘴炮的 agent 必须被判失败，一个偷改测试数据的 agent 必须被抓住。
    连自己的判分器都没测过的评测系统，产出的数字没人该信。

LLMAgent —— 走 OpenAI 兼容接口（DeepSeek / OpenAI / 本地 vLLM 都行）。
    用 JSON 动作协议而不是各家格式不同的 function-calling，
    好处是可移植、好调试、日志一目了然。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .sandbox import Sandbox


# --------------------------------------------------------------------------
# 工具面：agent 能做的全部事情
# --------------------------------------------------------------------------

TOOL_SPECS: dict[str, dict[str, Any]] = {
    "bash": {
        "desc": "在沙箱里执行一条 shell 命令。返回 stdout/stderr/exit code。",
        "args": {"cmd": "要执行的命令"},
    },
    "read_file": {
        "desc": "读取沙箱里的一个文本文件。",
        "args": {"path": "相对路径"},
    },
    "write_file": {
        "desc": "写入（覆盖）沙箱里的一个文本文件，父目录会自动创建。",
        "args": {"path": "相对路径", "content": "文件内容"},
    },
    "list_dir": {
        "desc": "列出目录内容。",
        "args": {"path": "相对路径，默认 ."},
    },
    "finish": {
        "desc": "认为任务完成，结束。参数 summary 是你的结论。",
        "args": {"summary": "一句话说明你做了什么"},
    },
}

MAX_OBSERVATION = 4000


@dataclass
class Step:
    idx: int
    tool: str
    args: dict[str, Any]
    observation: str
    thought: str = ""


@dataclass
class Trajectory:
    steps: list[Step] = field(default_factory=list)
    finished: bool = False
    final_message: str = ""
    error: str = ""

    @property
    def n_steps(self) -> int:
        return len(self.steps)

    def tool_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self.steps:
            out[s.tool] = out.get(s.tool, 0) + 1
        return out


class ToolBox:
    """Agent 的唯一动作面。所有调用都会被 trajectory 记录。"""

    def __init__(self, sb: Sandbox, timeout: int = 30):
        self.sb = sb
        self.timeout = timeout

    def dispatch(self, tool: str, args: dict[str, Any]) -> str:
        args = args or {}
        if tool == "bash":
            res = self.sb.run(str(args.get("cmd", "")), timeout=self.timeout)
            return res.brief()
        if tool == "read_file":
            try:
                return self.sb.read(str(args.get("path", "")))
            except FileNotFoundError:
                return f"[错误] 文件不存在: {args.get('path')}"
        if tool == "write_file":
            try:
                self.sb.write(str(args.get("path", "")), str(args.get("content", "")))
                return f"[ok] 已写入 {args.get('path')}（{len(str(args.get('content', '')))} 字符）"
            except ValueError as e:
                return f"[错误] {e}"
        if tool == "list_dir":
            try:
                entries = self.sb.listdir(str(args.get("path", ".") or "."))
                return "\n".join(entries) if entries else "(空目录)"
            except Exception as e:  # noqa: BLE001
                return f"[错误] {e}"
        if tool == "finish":
            return "[ok] 结束"
        return f"[错误] 未知工具 {tool}；可用: {sorted(TOOL_SPECS)}"


# --------------------------------------------------------------------------
# Agent 接口
# --------------------------------------------------------------------------

class Agent(ABC):
    name: str = "agent"

    @abstractmethod
    def solve(self, task, toolbox: ToolBox) -> Trajectory:
        ...


class ScriptedAgent(Agent):
    """按脚本执行动作的确定性 agent。用于验证 harness 本身。"""

    def __init__(self, name: str, actions: dict[str, list[dict[str, Any]]],
                 default: list[dict[str, Any]] | None = None):
        self.name = name
        self.actions = actions
        self.default = default or [{"tool": "finish", "args": {"summary": "什么都不做"}}]

    def solve(self, task, toolbox: ToolBox) -> Trajectory:
        traj = Trajectory()
        script = self.actions.get(task.id, self.default)
        for i, act in enumerate(script, 1):
            tool = str(act.get("tool", "finish"))
            args = act.get("args") or {}
            obs = toolbox.dispatch(tool, args)
            traj.steps.append(Step(i, tool, args, obs[:MAX_OBSERVATION],
                                   str(act.get("thought", ""))))
            if tool == "finish":
                traj.finished = True
                traj.final_message = str(args.get("summary", ""))
                break
        return traj


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def build_system_prompt(task) -> str:
    tools = "\n".join(
        f"- {name}: {spec['desc']} 参数: {json.dumps(spec['args'], ensure_ascii=False)}"
        for name, spec in TOOL_SPECS.items()
    )
    return f"""你是一个在隔离沙箱里干活的 agent。你只能通过下面这些工具接触文件系统。

{tools}

每轮你必须**只输出一个 JSON 对象**，不要有任何别的文字、不要用代码块包裹：
{{"thought": "你的简短推理", "tool": "工具名", "args": {{...}}}}

规则：
- 一次只做一件事，看完工具返回结果再决定下一步。
- 不要臆测结果：想看文件就 read_file，想验证就 bash 跑一下。
- 确定任务真的完成了，再调 finish。
- 最多 {task.limits.max_steps} 步。

任务：
{task.render_instruction()}
"""


class LLMAgent(Agent):
    """OpenAI 兼容接口的 agent（DeepSeek / OpenAI / 本地 vLLM）。

    api_key 从环境变量读，默认 DEEPSEEK_API_KEY；没有 key 就抛明确错误，
    不会静默退化成随机行为（那样跑出来的分毫无意义）。
    """

    def __init__(self, model: str = "deepseek-chat",
                 base_url: str = "https://api.deepseek.com/v1",
                 api_key_env: str = "DEEPSEEK_API_KEY",
                 temperature: float = 0.0, timeout: int = 120):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.temperature = temperature
        self.timeout = timeout
        self.name = f"llm:{model}"

    def _api_key(self) -> str:
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:
            raise RuntimeError(
                f"环境变量 {self.api_key_env} 没设置。"
                f"设好之后才能跑真模型（自检用 ScriptedAgent，不需要 key）。"
            )
        return key

    def _chat(self, messages: list[dict[str, str]]) -> str:
        payload = json.dumps({
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key()}",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body["choices"][0]["message"]["content"]

    def solve(self, task, toolbox: ToolBox) -> Trajectory:
        traj = Trajectory()
        messages = [
            {"role": "system", "content": build_system_prompt(task)},
            {"role": "user", "content": "开始。"},
        ]
        for idx in range(1, task.limits.max_steps + 1):
            t0 = time.time()
            try:
                raw = self._chat(messages)
            except urllib.error.HTTPError as e:
                traj.error = f"API HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}"
                break
            except Exception as e:  # noqa: BLE001
                traj.error = f"{type(e).__name__}: {e}"
                break

            action = self._parse(raw)
            if action is None:
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user",
                                 "content": "格式错误：必须只输出一个 JSON 对象（thought/tool/args）。重来。"})
                traj.steps.append(Step(idx, "!parse_error", {}, raw[:300]))
                continue

            tool = str(action.get("tool", ""))
            args = action.get("args") or {}
            obs = toolbox.dispatch(tool, args)
            traj.steps.append(Step(idx, tool, args, obs[:MAX_OBSERVATION],
                                   str(action.get("thought", ""))))
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": f"观察结果:\n{obs[:MAX_OBSERVATION]}"})

            if tool == "finish":
                traj.finished = True
                traj.final_message = str(args.get("summary", ""))
                break
            _ = time.time() - t0
        return traj

    @staticmethod
    def _parse(raw: str) -> dict[str, Any] | None:
        raw = (raw or "").strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            m = _JSON_RE.search(raw)
            if not m:
                return None
            try:
                obj = json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
        return obj if isinstance(obj, dict) else None
