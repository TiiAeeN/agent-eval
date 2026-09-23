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
from .memory import MemoryStore
from .sandbox import Sandbox
from .users import ScriptedUser


# --------------------------------------------------------------------------
# 对话记录
# --------------------------------------------------------------------------

@dataclass
class Turn:
    idx: int
    speaker: str          # "user" | "agent"
    text: str
    session: int = 1      # 第几次会话（长期记忆测试会跑多段）


@dataclass
class Transcript:
    """这场对话的全部痕迹。判分器读它，agent 改不了它。"""

    turns: list[Turn] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    session: int = 1

    def add(self, speaker: str, text: str) -> None:
        self.turns.append(Turn(len(self.turns) + 1, speaker, text, self.session))

    def start_session(self, n: int) -> None:
        self.session = n

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
        out: list[str] = []
        last = None
        for t in self.turns:
            if t.session != last:
                if last is not None:
                    out.append(f"  ────── 第 {t.session} 次会话 ──────")
                last = t.session
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
                 transcript: Transcript, memory: MemoryStore | None = None,
                 timeout: int = 30):
        super().__init__(sb, timeout)
        self.backend = backend
        self.transcript = transcript
        self.memory = memory
        self.said: list[str] = []
        self.finished = False

    def specs(self) -> dict[str, dict[str, Any]]:
        out = dict(TOOL_SPECS)
        out.update(DIALOGUE_TOOL_SPECS)
        out.update(self.backend.tool_specs())
        if self.memory is not None:
            out.update(self.memory.tool_specs())
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
        if self.memory is not None and tool in self.memory.tool_specs():
            return self.memory.call(tool, args)
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
- **先把该办的事办完**（该查的查、该核对的核对、该退的退、该转人工的转人工），
  最后才对用户说话。说一句 say 就等于把话交回给用户了 —— 别先寒暄再办事，
  否则这一轮就白费了。
- 不要臆测：要查订单就 lookup_order，不要凭想象判断。
- 不要重复劳动：已经查过、核对过的事情，不要再做一遍。
- 拿不准的事，宁可转人工，也不要替用户做主。
- **事情有结论时（同意退 / 拒绝 / 转人工），必须调 conclude 交卷**，再调 finish 结束。
- conclude 的三格必须从它规定的取值里选，不许自己造词。

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
    memory_state: dict[str, Any] = field(default_factory=dict)
    sessions: list[dict[str, Any]] = field(default_factory=list)


# 用户不说话了、而 agent 还没交卷时，由考场再推它一把：
# 「对方不说了，把你的结论交上来」。最多推这么多轮。
WRAPUP_LINE = "（对方不再说话了。请把你的处理结论交上来。）"
WRAPUP_ROUNDS = 2


def _session_loop(user, agent: DialogueAgent, toolbox: DialogueToolBox,
                  transcript: Transcript, max_turns: int) -> tuple[int, bool]:
    """跑一段会话。返回 (用掉几轮, 这一段落有没有主动收尾)。"""
    turn, wrapups = 0, 0
    user_msg = user.next_message(transcript.user_lines())
    while turn < max_turns:
        if user_msg is None:
            # 对方不说话了。但对话不能就这么断 ——
            # agent 可能还欠着一个结论，考场得给它机会交上来。
            if toolbox.finished or wrapups >= WRAPUP_ROUNDS:
                break
            wrapups += 1
            user_msg = WRAPUP_LINE
        turn += 1
        transcript.add("user", user_msg)
        agent.respond(user_msg, transcript, toolbox)
        if toolbox.finished:
            break
        user_msg = user.next_message(transcript.user_lines())
    return turn, toolbox.finished


def run_dialogue_once(task, agent: DialogueAgent, user=None, backend: DialogueBackend = None,
                      sb: Sandbox = None, max_turns: int | None = None,
                      memory: MemoryStore | None = None,
                      sessions: list[list[str]] | None = None,
                      closing: str | None = None) -> DialogueResult:
    """跑一场对话。可以是一段，也可以是**多段会话**。

    多段会话（长期记忆测试用）：同一块记忆、同一个后端，用户隔几天来一次。
    每段之间只换剧本 —— 记忆、工单、后端状态全都留着，那才叫「长期」。
    """
    transcript = Transcript()
    toolbox = DialogueToolBox(sb, backend, transcript, memory=memory,
                              timeout=task.limits.timeout_sec)
    toolbox.task = task          # agent 从这里拿任务说明（作业规范写在 instruction 里）
    max_turns = max_turns or task.limits.max_steps
    error = ""
    session_log: list[dict[str, Any]] = []
    t0 = time.perf_counter()

    for no, script in enumerate(sessions if sessions else [None], 1):
        transcript.start_session(no)
        toolbox.finished = False        # 上一通电话结束了，这一通是新的
        sim = user if script is None else ScriptedUser(list(script),
                                                      closing=closing or None)
        try:
            used, done = _session_loop(sim, agent, toolbox, transcript, max_turns)
        except Exception as e:  # noqa: BLE001 —— agent 崩了记成一次失败运行，不中断整轮
            error = f"{type(e).__name__}: {e}"
            break
        session_log.append({"session": no, "turns": used, "finished": done})

    return DialogueResult(
        task_id=task.id,
        transcript=transcript,
        trajectory=Trajectory(steps=list(transcript.steps), finished=toolbox.finished),
        backend_state=backend.snapshot(),
        memory_state=memory.snapshot() if memory is not None else {},
        turns=sum(s["turns"] for s in session_log),
        sessions=session_log,
        finished=toolbox.finished,
        wall_time=time.perf_counter() - t0,
        error=error,
    )


# --------------------------------------------------------------------------
# 真模型考生
# --------------------------------------------------------------------------

class LLMDialogueAgent(DialogueAgent):
    """真模型当考生。走 OpenAI 兼容接口（DeepSeek / OpenAI / 本地 vLLM 都行）。

    一轮里可以连调几个工具；**调了 say 就算这一轮说完**，把话交回给用户。

    它是无状态的（全靠外部传进来的 transcript），但还是按工厂来建 ——
    接口统一，也不给"复用对象"留口子。

    ⚠ 模型调用失败必须**大声崩**，让这轮记成失败。绝不能静默变成"通过"。
    """

    def __init__(self, model: str = "deepseek-chat",
                 base_url: str = "https://api.deepseek.com/v1",
                 api_key_env: str = "DEEPSEEK_API_KEY",
                 temperature: float = 0.0, timeout: int = 120,
                 max_calls_per_turn: int = 4):
        from .agents import LLMAgent          # 复用它的 HTTP 客户端，不另写一份
        self._llm = LLMAgent(model=model, base_url=base_url, api_key_env=api_key_env,
                             temperature=temperature, timeout=timeout)
        self.name = f"llm-dialogue:{model}"
        self.max_calls_per_turn = max_calls_per_turn
        self._messages: list[dict[str, str]] | None = None   # 跨轮延续，别每轮重建

    def respond(self, user_msg: str, transcript: Transcript,
                toolbox: DialogueToolBox) -> None:
        """⚠ messages 必须**留在自己身上**，跨轮延续。

        早先的写法是每轮从 transcript 重建 messages ——
        可 transcript 里只有"它说过的话"，**没有它调过的工具**。
        结果：模型每轮都失忆重来，把 lookup_order / verify_identity
        反复做五遍，永远推进不到下一步。
        （踩过的坑：现象是"它死活不交卷"，根因是记忆被我弄丢了。）
        """
        task = getattr(toolbox, "task", None)
        if task is None:
            raise RuntimeError("LLMDialogueAgent 拿不到 task —— 运行器得先把 task 放进 toolbox")

        if self._messages is None:
            self._messages = [{"role": "system",
                               "content": build_dialogue_prompt(task, toolbox)}]
        self._messages.append({"role": "user", "content": user_msg})

        concluded = False
        for _ in range(self.max_calls_per_turn):
            try:
                raw = self._llm._chat(self._messages)
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(f"模型调用失败: {type(e).__name__}: {e}") from e

            action = self._llm._parse(raw)
            if action is None:
                self._messages.append({"role": "assistant", "content": raw})
                self._messages.append({"role": "user",
                                       "content": "格式错误：必须只输出一个 JSON 对象"
                                                  "（thought/tool/args）。重来。"})
                continue

            tool = str(action.get("tool", ""))
            obs = toolbox.dispatch(tool, action.get("args") or {})
            self._messages.append({"role": "assistant", "content": raw})

            if tool == "conclude":
                concluded = True

            if tool == "finish":
                return

            # 「说一句话」通常等于把话交回给用户，这一轮就该结束了。
            # **但交过卷之后不一样**：那已经是收尾阶段，它还要把结论告诉用户、
            # 再调 finish 收工。这时候在 say 处截断，等于把已经早停的 agent
            # 硬拉回来又问一遍 —— 量出来的"没早停"是假的。
            if tool == "say" and not concluded:
                return

            self._messages.append({"role": "user",
                                   "content": f"观察结果:\n{obs[:MAX_OBSERVATION]}"})

        # 一轮里工具次数用完还没跟用户说话。
        # 这里**故意什么都不补** —— 宁可让记录里留着"这一轮它没回话"，
        # 也不要替它编一句，那会污染判分。
        return
