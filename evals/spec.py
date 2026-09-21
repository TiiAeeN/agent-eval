"""任务定义与加载。

一个 Task = 指令 + 初始状态 + 校验规则 + 限制。

任务用 task.yml 描述，初始文件可以直接写在 yml 里，也可以放在
同级 files/ 目录下（适合多文件、带特殊字符的场景）。
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
    source_dir: Path | None = None

    def render_instruction(self) -> str:
        return self.instruction.strip()

    def __str__(self) -> str:  # pragma: no cover
        return f"<Task {self.id} [{self.category}/{self.difficulty}]>"


def _norm_rel(rel: str) -> str:
    return rel.replace("\\", "/").lstrip("./")


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
            max_steps=int(lim.get("max_steps", 12)),
            timeout_sec=int(lim.get("timeout_sec", 60)),
        ),
        source_dir=task_dir,
    )


def discover_tasks(tasks_root: str | Path) -> list[Task]:
    """按目录名排序扫描任务，保证每次跑的**顺序**也一致。"""
    root = Path(tasks_root)
    if not root.is_dir():
        raise NotADirectoryError(f"任务目录不存在: {root}")
    out: list[Task] = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if (d / "task.yml").exists() or (d / "task.yaml").exists():
            out.append(load_task(d))
    return out
