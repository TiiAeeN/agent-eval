"""对话型考场：用户模拟器 + 假后端 + 对话循环。

工具型考场里，agent 面对的是死文件。对话型考场多了两样东西：

    用户 —— 会回应、会追问、会故意发一句乱码     （users.py 已经写好）
    后端 —— agent 的动作真正改到的那个东西        （这里定义接口）

判分的对象也跟着变了：不再只是「文件长什么样」，而是

    后端最后变成什么样   +   agent 到底说了什么、没说什么

铁律不变，而且更硬：
  - 后端规则写在宿主机上，agent 只能通过工具碰它
  - 对话和每一次工具调用全部留痕；判分只看状态和记录，不看 agent 的自述
  - agent 想对用户说话，唯一的通道是 say 工具 —— 于是「它有没有把用户晾着」
    变成一件可以被自动判定的事
"""
from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .agents import MAX_OBSERVATION, TOOL_SPECS, Step, ToolBox, Trajectory
from .sandbox import Sandbox


# --------------------------------------------------------------------------
# 对话记录
# --------------------------------------------------------------------------

@dataclass
class Turn:
    idx: int
    speaker: str          # "user" | "agent"
    text: str


@dataclass
class Transcript:
    """这场对话的全部痕迹。判分器读它，agent 改不了它。"""

    turns: list[Turn] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)

    def add(self, speaker: str, text: str) -> None:
        self.turns.append(Turn(len(self.turns) + 1, speaker, text))

    def user_lines(self) -> list[str]:
        return [t.text for t in self.turns if t.speaker == "user"]

    def agent_lines(self) -> list[str]:
        return [t.text for t in self.turns if t.speaker == "agent"]

    def said_anything(self) -> bool:
        """agent 这一路有没有回过话。"""
        return bool(self.agent_lines())

    def agent_said(self, pattern: str) -> bool:
        """agent 说过的话里有没有命中这个正则。"""
        return any(re.search(pattern, t) for t in self.agent_lines())

    def tools_called(self) -> list[str]:
        return [s.tool for s in self.steps]

    def called(self, tool: str) -> int:
        return sum(1 for s in self.steps if s.tool == tool)

    def render(self) -> str:
        out = []
        for t in self.turns:
            who = "用户" if t.speaker == "user" else "agent"
            out.append(f"  {t.idx:>2}. [{who}] {t.text}")
        return "\n".join(out)


# --------------------------------------------------------------------------
# 假后端接口
# --------------------------------------------------------------------------

class DialogueBackend(ABC):
    """agent 的动作真正改到的那个东西。

    实现要点：
      - tool_specs() 声明它暴露哪些动作（会拼进系统提示词）
      - call() 执行动作，返回给 agent 看的文本
      - snapshot() 导出当前状态，**只给判分器读**
    """

    @abstractmethod
    def tool_specs(self) -> dict[str, dict[str, Any]]:
        ...

    @abstractmethod
    def call(self, tool: str, args: dict[str, Any]) -> str:
        ...

    @abstractmethod
    def snapshot(self) -> dict[str, Any]:
        ...


# --------------------------------------------------------------------------
# 对话型工具面
# --------------------------------------------------------------------------

DIALOGUE_TOOL_SPECS: dict[str, dict[str, Any]] = {
    "say": {
        "desc": "对用户说话。这是你唯一能让用户看到东西的方式。不回话就等于把用户晾着。",
        "args": {"text": "你要对用户说的话"},
    },
    "finish": {
        "desc": "结束这次对话。参数 summary 用一句话说明你的处理结果。",
        "args": {"summary": "处理结论"},
    },
}


class DialogueToolBox(ToolBox):
    """对话型考场里 agent 的动作面 = 文件工具 + say + 后端动作。

    它比工具型的 ToolBox 多干一件事：**把每一次调用都记进 transcript**。
    agent 够不着 transcript，所以这份记录是可信的。
    """

    def __init__(self, sb: Sandbox, backend: DialogueBackend,
                 transcript: Transcript, timeout: int = 30):
        super().__init__(sb, timeout)
        self.backend = backend
        self.transcript = transcript
        self.said: list[str] = []
        self.finished = False

    def specs(self) -> dict[str, dict[str, Any]]:
        out = dict(TOOL_SPECS)
        out.update(DIALOGUE_TOOL_SPECS)
        out.update(self.backend.tool_specs())
        return out

    def dispatch(self, tool: str, args: dict[str, Any]) -> str:
        result = self._run(tool, args or {})
        self.transcript.steps.append(Step(
            idx=len(self.transcript.steps) + 1,
            tool=tool,
            args=args or {},
            observation=result[:MAX_OBSERVATION],
        ))
        return result

    def _run(self, tool: str, args: dict[str, Any]) -> str:
        if tool == "say":
            text = str(args.get("text", ""))
            self.said.append(text)
            self.transcript.add("agent", text)
            return "[ok] 已发给用户"
        if tool == "finish":
            self.finished = True
            return "[ok] 结束"
        if tool in self.backend.tool_specs():
            return self.backend.call(tool, args)
        return super().dispatch(tool, args)


# --------------------------------------------------------------------------
# 对话型 agent 接口
# --------------------------------------------------------------------------

class DialogueAgent(ABC):
    """对话型 agent。

    和 Agent(工具型) 的区别：没有 solve()，只有 respond()。
    一轮里 agent 可以调任意多个工具，但**想跟用户说话必须调 say**。
    """

    name: str = "agent"

    @abstractmethod
    def respond(self, user_msg: str, transcript: Transcript,
                toolbox: DialogueToolBox) -> None:
        ...


class ScriptedDialogueAgent(DialogueAgent):
    """按剧本行动的确定性对话 agent。用于验证对话型判分器本身。

    turns 是二维列表：每个元素 = 那一轮要做的动作序列。

        ScriptedDialogueAgent("good", [
            [{"tool": "lookup_order", "args": {"order_id": "A1234"}},
             {"tool": "say", "args": {"text": "请提供完整姓名和后四位手机号"}}],
            [{"tool": "verify_identity", "args": {...}},
             {"tool": "say", "args": {"text": "核对通过"}}],
        ])

    ⚠ 它**有状态**（记着自己演到第几轮了）。跑重复必须是新对象 ——
      这跟 ScriptedUser 那个坑一模一样，别在评测器自己身上再踩一次。
    """

    def __init__(self, name: str, turns: list[list[dict[str, Any]]]):
        self.name = name
        self.turns = turns
        self.i = 0

    def respond(self, user_msg: str, transcript: Transcript,
                toolbox: DialogueToolBox) -> None:
        actions = self.turns[self.i] if self.i < len(self.turns) else []
        self.i += 1
        for act in actions:
            toolbox.dispatch(str(act.get("tool", "")), act.get("args") or {})


def build_dialogue_prompt(task, toolbox: DialogueToolBox) -> str:
    """对话型任务的系统提示词（LLM agent 用）。"""
    tools = "\n".join(
        f"- {name}: {spec['desc']} 参数: {json.dumps(spec['args'], ensure_ascii=False)}"
        for name, spec in toolbox.specs().items()
    )
    return f"""你是一个客服 agent，正在跟一位真实用户对话。

用户能看到的东西**只有你通过 say 发出去的文字**。你调用的其他工具，用户看不见。

可用工具：
{tools}

每一轮你必须**只输出一个 JSON 对象**，不要有任何别的文字、不要用代码块包裹：
{{"thought": "你的简短推理", "tool": "工具名", "args": {{...}}}}

规则：
- 一次只做一件事，看完返回结果再决定下一步。
- 用户说了话，你必须**先回应**，不要闷头办自己的事。
- 不要臆测：要查订单就 lookup_order，不要凭想象判断。
- 拿不准的事，宁可问用户，也不要替他做主。
- 处理完了，先 say 告诉用户结果，再 finish。
- 最多 {task.limits.max_steps} 轮。

任务：
{task.render_instruction()}
"""


# --------------------------------------------------------------------------
# 对话循环
# --------------------------------------------------------------------------

@dataclass
class DialogueResult:
    task_id: str
    transcript: Transcript
    trajectory: Trajectory
    backend_state: dict[str, Any]
    turns: int
    finished: bool
    wall_time: float
    error: str = ""


def run_dialogue_once(task, agent: DialogueAgent, user, backend: DialogueBackend,
                      sb: Sandbox, max_turns: int | None = None) -> DialogueResult:
    """跑一场对话。

    流程：用户先说 → agent 回应（可能连调几个工具）→ 用户再说 → …
    终止条件：用户没话了（next_message 返回 None）/ agent 调了 finish / 轮数用完。
    """
    transcript = Transcript()
    toolbox = DialogueToolBox(sb, backend, transcript, timeout=task.limits.timeout_sec)
    max_turns = max_turns or task.limits.max_steps
    error = ""
    turn = 0
    t0 = time.perf_counter()

    try:
        user_msg = user.next_message(transcript.user_lines())
        while user_msg is not None and turn < max_turns:
            turn += 1
            transcript.add("user", user_msg)
            agent.respond(user_msg, transcript, toolbox)
            if toolbox.finished:
                break
            user_msg = user.next_message(transcript.user_lines())
    except Exception as e:  # noqa: BLE001 —— agent 崩了记成一次失败运行，不中断整轮
        error = f"{type(e).__name__}: {e}"

    return DialogueResult(
        task_id=task.id,
        transcript=transcript,
        trajectory=Trajectory(steps=list(transcript.steps), finished=toolbox.finished),
        backend_state=backend.snapshot(),
        turns=turn,
        finished=toolbox.finished,
        wall_time=time.perf_counter() - t0,
        error=error,
    )
