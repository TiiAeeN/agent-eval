"""任务定义与加载。

一个 Task = 指令 + 初始状态 + 校验规则 + 限制。

任务用 task.yml 描述，初始文件可以直接写在 yml 里，也可以放在
同级 files/ 目录下（适合多文件、带特殊字符的场景）。

两种任务：
    工具型 —— 发指令、改文件、判文件（DialogueSpec 为 None）
    对话型 —— 多一块 dialogue:（用户剧本、期望答案、期望后端状态、轮数上限）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Limits:
    max_steps: int = 12
    timeout_sec: int = 60


@dataclass
class DialogueSpec:
    """对话型任务多出来的那一块。工具型任务没有它。

    user_script  用户剧本（ScriptedUser 的台词，一句一行）
    expect       期望的三格答案（conclusion）
    expect_backend 期望的后端状态
    max_turns    轮数上限 —— 「早停」唯一能被机器判的形式：
                 卡得比剧本句数短，agent 主动 finish 才过得去
    """
    user_script: list[str] = field(default_factory=list)
    order_id: str = ""
    max_turns: int | None = None
    closing: str = ""
    # 长期记忆测试：一次运行跑多段会话，共享同一块记忆和同一个后端
    sessions: list[list[str]] = field(default_factory=list)
    memory: dict[str, str] = field(default_factory=dict)
    expect: dict[str, str] = field(default_factory=dict)
    expect_backend: dict[str, Any] = field(default_factory=dict)


@dataclass
class Task:
    id: str
    name: str
    instruction: str
    checks: list[dict[str, Any]]
    files: dict[str, str] = field(default_factory=dict)
    protected: list[str] = field(default_factory=list)
    category: str = "general"
    difficulty: str = "normal"
    limits: Limits = field(default_factory=Limits)
    dialogue: DialogueSpec | None = None
    source_dir: Path | None = None

    @property
    def is_dialogue(self) -> bool:
        return self.dialogue is not None

    def render_instruction(self) -> str:
        return self.instruction.strip()

    def __str__(self) -> str:  # pragma: no cover
        kind = "对话型" if self.is_dialogue else "工具型"
        return f"<Task {self.id} [{kind}/{self.category}/{self.difficulty}]>"


def _norm_rel(rel: str) -> str:
    return rel.replace("\\", "/").lstrip("./")


def _parse_dialogue(raw: Any, fallback_max_steps: int) -> DialogueSpec | None:
    if not raw:
        return None
    mt = raw.get("max_turns")
    return DialogueSpec(
        user_script=[str(s) for s in (raw.get("user_script") or [])],
        order_id=str(raw.get("order_id", "")),
        max_turns=int(mt) if mt is not None else fallback_max_steps,
        # 剧本说完了，用户还得能接得住话 —— 否则 agent 问一句没人应，就卡死
        closing=str(raw.get("closing", "好的，那就麻烦你帮我办一下吧。")),
        sessions=[[str(s) for s in seg] for seg in (raw.get("sessions") or [])],
        memory={str(k): str(v) for k, v in (raw.get("memory") or {}).items()},
        expect={str(k): str(v) for k, v in (raw.get("expect") or {}).items()},
        expect_backend=dict(raw.get("expect_backend") or {}),
    )


def load_task(task_dir: str | Path) -> Task:
    task_dir = Path(task_dir)
    meta_path = task_dir / "task.yml"
    if not meta_path.exists():
        meta_path = task_dir / "task.yaml"
    if not meta_path.exists():
        raise FileNotFoundError(f"{task_dir} 里没有 task.yml")

    meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}

    files: dict[str, str] = {}
    for rel, content in ((meta.get("setup") or {}).get("files") or {}).items():
        files[_norm_rel(rel)] = content if isinstance(content, str) else str(content)

    fixture_dir = task_dir / "files"
    if fixture_dir.is_dir():
        for p in sorted(fixture_dir.rglob("*")):
            if p.is_file():
                rel = p.relative_to(fixture_dir).as_posix()
                files.setdefault(rel, p.read_text(encoding="utf-8"))

    lim = meta.get("limits") or {}
    max_steps = int(lim.get("max_steps", 12))
    return Task(
        id=meta["id"],
        name=meta.get("name", meta["id"]),
        instruction=meta["instruction"],
        checks=list(meta.get("checks") or []),
        files=files,
        protected=[_norm_rel(p) for p in (meta.get("protected") or [])],
        category=meta.get("category", "general"),
        difficulty=meta.get("difficulty", "normal"),
        limits=Limits(
            max_steps=max_steps,
            timeout_sec=int(lim.get("timeout_sec", 60)),
        ),
        dialogue=_parse_dialogue(meta.get("dialogue"), max_steps),
        source_dir=task_dir,
    )


def discover_tasks(tasks_root: str | Path, kind: str | None = None) -> list[Task]:
    """按目录名排序扫描任务，保证每次跑的**顺序**也一致。

    kind: None=全部 / "tool"=只要工具型 / "dialogue"=只要对话型。
    两种任务跑的路径完全不同，混在一起跑会把对方的 agent 判成失败 ——
    所以取任务的时候就得分开。
    """
    root = Path(tasks_root)
    if not root.is_dir():
        raise NotADirectoryError(f"任务目录不存在: {root}")
    if kind not in (None, "tool", "dialogue"):
        raise ValueError(f'kind 只能是 None / "tool" / "dialogue"，收到 {kind!r}')
    out: list[Task] = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if (d / "task.yml").exists() or (d / "task.yaml").exists():
            t = load_task(d)
            if kind == "tool" and t.is_dialogue:
                continue
            if kind == "dialogue" and not t.is_dialogue:
                continue
            out.append(t)
    return out
