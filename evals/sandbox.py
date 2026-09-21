"""沙箱：每次跑都在一个干净的工作目录里。

隔离是分层的，别把两种后端混为一谈：

    LocalSandbox  —— 只保证「文件状态隔离」：每次跑前清空重建工作目录，
                     上次留下的垃圾不会污染下次。零依赖，本机就能跑。
    DockerSandbox —— 额外提供「系统级隔离」：命令在容器里执行，挂载工作目录，
                     默认断网。需要本机装 docker。

上层代码（runner / checks）只依赖 Sandbox 接口，换后端不用改一行。

另外一个容易被忽略的复现细节：**编码**。中文 Windows 下子进程默认吐 GBK 字节，
同一个任务在你的机器和 CI 上会得到不同结果（甚至直接 UnicodeDecodeError 崩掉）。
所以这里统一 PYTHONIOENCODING=utf-8 + errors="replace"，把环境差异钉死。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ExecResult:
    cmd: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def brief(self, limit: int = 400) -> str:
        body = (self.stdout or "")
        if self.stderr:
            body += "\n[stderr]\n" + self.stderr
        body = body.strip()
        if len(body) > limit:
            body = body[:limit] + f"...(+{len(body) - limit} chars)"
        flags = "TIMEOUT " if self.timed_out else ""
        return f"{flags}exit={self.exit_code}\n{body}"


class Sandbox(ABC):
    """工作目录 + 命令执行的抽象。"""

    path: Path

    # ---------- 生命周期 ----------
    @abstractmethod
    def reset(self, files: dict[str, str], dirs: list[str] | None = None) -> None:
        """清空并重建工作目录到初始状态。"""

    @abstractmethod
    def run(self, cmd: str, timeout: int = 30) -> ExecResult:
        """在沙箱内执行一条命令。"""

    @abstractmethod
    def close(self) -> None:
        """销毁沙箱。"""

    # ---------- 文件访问（宿主机侧；agent 没有这些 API，它只有工具） ----------
    def abspath(self, rel: str) -> Path:
        root = self.path.resolve()
        p = (root / rel).resolve()
        if p != root and root not in p.parents:
            raise ValueError(f"路径越界，拒绝访问: {rel}")
        return p

    def read(self, rel: str) -> str:
        p = self.abspath(rel)
        if not p.exists():
            raise FileNotFoundError(rel)
        return p.read_text(encoding="utf-8", errors="replace")

    def write(self, rel: str, content: str) -> None:
        p = self.abspath(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def exists(self, rel: str) -> bool:
        return self.abspath(rel).exists()

    def listdir(self, rel: str = ".") -> list[str]:
        p = self.abspath(rel) if rel not in (".", "") else self.path
        if not p.exists():
            return []
        return sorted(x.name + ("/" if x.is_dir() else "") for x in p.iterdir())

    # ---------- 指纹：证明「环境一致」「文件没被动过」 ----------
    def state_hash(self) -> str:
        h = hashlib.sha256()
        for p in sorted(self.path.rglob("*")):
            if p.is_file():
                rel = p.relative_to(self.path).as_posix()
                h.update(rel.encode("utf-8"))
                h.update(b"\0")
                h.update(p.read_bytes())
                h.update(b"\0")
        return h.hexdigest()[:16]

    def file_hash(self, rel: str) -> str:
        p = self.abspath(rel)
        if not p.exists():
            return ""
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


class LocalSandbox(Sandbox):
    """临时目录 + 子进程。零依赖，缺点是没有系统级隔离。"""

    def __init__(self, root: str | Path | None = None, keep: bool = False):
        base = Path(root) if root else Path(tempfile.gettempdir()) / "agent-eval-runs"
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / f"run-{uuid.uuid4().hex[:8]}"
        self.path.mkdir(parents=True, exist_ok=True)
        self.keep = keep

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        # 让 `python` 指向跑本 harness 的解释器，不受用户 PATH 影响
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONUTF8"] = "1"
        # 跑评测时不要代理，否则网络类任务的成败取决于代理状态
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                  "http_proxy", "https_proxy", "all_proxy"):
            env.pop(k, None)
        return env

    def reset(self, files: dict[str, str], dirs: list[str] | None = None) -> None:
        if self.path.exists():
            shutil.rmtree(self.path, ignore_errors=True)
        self.path.mkdir(parents=True, exist_ok=True)
        for d in dirs or []:
            (self.path / d).mkdir(parents=True, exist_ok=True)
        for rel, content in files.items():
            self.write(rel, content)

    def run(self, cmd: str, timeout: int = 30) -> ExecResult:
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=str(self.path), env=self._env(),
                capture_output=True, timeout=timeout,
            )
            return ExecResult(
                cmd, proc.returncode,
                proc.stdout.decode("utf-8", "replace"),
                proc.stderr.decode("utf-8", "replace"),
            )
        except subprocess.TimeoutExpired as e:
            return ExecResult(
                cmd, -1,
                (e.stdout or b"").decode("utf-8", "replace"),
                (e.stderr or b"").decode("utf-8", "replace"),
                timed_out=True,
            )
        except OSError as e:
            return ExecResult(cmd, -2, "", f"无法启动进程: {e}")

    def close(self) -> None:
        if not self.keep:
            shutil.rmtree(self.path, ignore_errors=True)


class DockerSandbox(Sandbox):
    """容器级隔离：命令在容器内执行，工作目录挂载进去，默认断网。

    没有 docker 就**明确报错**——不静默降级成 LocalSandbox，
    否则你以为跑的是隔离环境，其实不是，这种评测结果一文不值。
    """

    def __init__(self, image: str = "python:3.12-slim", root: str | Path | None = None,
                 keep: bool = False, network: str = "none"):
        if shutil.which("docker") is None:
            raise RuntimeError(
                "DockerSandbox 需要 docker，本机没装。\n"
                "装 Docker Desktop（或 WSL2 + docker）后再用；"
                "现在可以先跑 LocalSandbox。"
            )
        self.image = image
        self.network = network
        base = Path(root) if root else Path(tempfile.gettempdir()) / "agent-eval-runs"
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / f"run-{uuid.uuid4().hex[:8]}"
        self.path.mkdir(parents=True, exist_ok=True)
        self.keep = keep

    def reset(self, files: dict[str, str], dirs: list[str] | None = None) -> None:
        if self.path.exists():
            shutil.rmtree(self.path, ignore_errors=True)
        self.path.mkdir(parents=True, exist_ok=True)
        for d in dirs or []:
            (self.path / d).mkdir(parents=True, exist_ok=True)
        for rel, content in files.items():
            self.write(rel, content)

    def run(self, cmd: str, timeout: int = 30) -> ExecResult:
        mount = f"{self.path.resolve()}:/work"
        docker_cmd = [
            "docker", "run", "--rm", "-i",
            "--network", self.network,
            "--memory", "1g", "--cpus", "2",
            "-v", mount, "-w", "/work",
            "-e", "PYTHONIOENCODING=utf-8",
            self.image, "sh", "-lc", cmd,
        ]
        try:
            proc = subprocess.run(docker_cmd, capture_output=True, timeout=timeout)
            return ExecResult(
                cmd, proc.returncode,
                proc.stdout.decode("utf-8", "replace"),
                proc.stderr.decode("utf-8", "replace"),
            )
        except subprocess.TimeoutExpired:
            return ExecResult(cmd, -1, "", "", timed_out=True)
        except OSError as e:
            return ExecResult(cmd, -2, "", f"docker 调用失败: {e}")

    def close(self) -> None:
        if not self.keep:
            shutil.rmtree(self.path, ignore_errors=True)
