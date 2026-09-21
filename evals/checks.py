"""校验器 —— 跑在沙箱外面，只看最终状态。

这是整个项目最容易被做烂、也最值钱的一层。三条铁律：

 1. **校验逻辑在宿主机上**。agent 只能通过工具改沙箱里的文件；
    check 的代码它既看不到也改不到。所以「agent 说自己做完了」永远不算数。
 2. **只看状态，不看过程**。agent 说要怎么干、干了多久，都不影响判分。
 3. **校验器自己不能崩**。未知 kind、文件不存在、编码异常 —— 一律返回
    「失败 + 原因」，而不是抛异常中断整轮评测（否则一个坏任务毁掉整个报告）。
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from typing import Any

from .sandbox import Sandbox

# check kind -> 需要的参数
KNOWN_KINDS = {
    "file_exists", "file_absent", "file_contains", "file_not_contains",
    "file_equals", "file_unchanged", "regex_match", "json_field",
    "csv_equals", "command_ok", "command_output_contains", "dir_equals",
}


@dataclass
class CheckResult:
    kind: str
    target: str
    passed: bool
    detail: str = ""

    def line(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        tail = f"  ({self.detail})" if self.detail else ""
        return f"[{mark}] {self.kind}: {self.target}{tail}"


def _norm(s: str) -> str:
    return s.replace("\r\n", "\n").strip()


def run_checks(checks: list[dict[str, Any]], sb: Sandbox,
               protected_hashes: dict[str, str] | None = None) -> list[CheckResult]:
    return [_run_one(c or {}, sb, protected_hashes or {}) for c in checks]


def _fail(kind: str, target: str, detail: str) -> CheckResult:
    return CheckResult(kind, target, False, detail)


def _ok(kind: str, target: str, detail: str = "") -> CheckResult:
    return CheckResult(kind, target, True, detail)


def _run_one(c: dict[str, Any], sb: Sandbox,
             protected_hashes: dict[str, str]) -> CheckResult:
    kind = str(c.get("kind") or "?")
    path = str(c.get("path") or "")
    try:
        if kind not in KNOWN_KINDS:
            return _fail(kind, path, f"未知的 check kind（拼错了？）已知: {sorted(KNOWN_KINDS)}")

        if kind == "file_exists":
            return _ok(kind, path) if sb.exists(path) else _fail(kind, path, "文件不存在")

        if kind == "file_absent":
            return _fail(kind, path, "文件仍存在（要求删除）") if sb.exists(path) else _ok(kind, path)

        if kind in ("file_contains", "file_not_contains"):
            if not sb.exists(path):
                return _fail(kind, path, "文件不存在")
            text = sb.read(path)
            needle = str(c.get("text", ""))
            hit = needle in text
            if kind == "file_contains":
                return _ok(kind, path, f"找到 {needle!r}") if hit else _fail(kind, path, f"没找到 {needle!r}")
            return _fail(kind, path, f"不该出现 {needle!r}") if hit else _ok(kind, path)

        if kind == "file_equals":
            if not sb.exists(path):
                return _fail(kind, path, "文件不存在")
            want = _norm(str(c.get("text", "")))
            got = _norm(sb.read(path))
            if got == want:
                return _ok(kind, path)
            return _fail(kind, path, f"内容不符；期望 {want[:60]!r}，实际 {got[:60]!r}")

        if kind == "file_unchanged":
            want = str(c.get("hash") or protected_hashes.get(path, ""))
            got = sb.file_hash(path)
            if not got:
                return _fail(kind, path, "文件不存在")
            if not want:
                return _fail(kind, path, "没有基线哈希可比对")
            return _ok(kind, path) if got == want else _fail(
                kind, path, f"被改动了（基线 {want[:8]} → 现在 {got[:8]}）")

        if kind == "regex_match":
            if not sb.exists(path):
                return _fail(kind, path, "文件不存在")
            pattern = str(c.get("pattern", ""))
            if re.search(pattern, sb.read(path), flags=re.MULTILINE):
                return _ok(kind, path, f"/{pattern}/ 命中")
            return _fail(kind, path, f"/{pattern}/ 没命中")

        if kind == "json_field":
            if not sb.exists(path):
                return _fail(kind, path, "文件不存在")
            data = json.loads(sb.read(path))
            key = str(c.get("key", ""))
            cur: Any = data
            for part in key.split("."):
                if isinstance(cur, dict) and part in cur:
                    cur = cur[part]
                else:
                    return _fail(kind, path, f"键 {key} 不存在")
            if "equals" in c:
                want = c["equals"]
                return _ok(kind, path, f"{key}={cur!r}") if cur == want else _fail(
                    kind, path, f"{key}={cur!r}，期望 {want!r}")
            if "in" in c:
                opts = c["in"]
                return _ok(kind, path, f"{key}={cur!r}") if cur in opts else _fail(
                    kind, path, f"{key}={cur!r} 不在 {opts!r} 里")
            return _ok(kind, path, f"{key}={cur!r}（未指定期望值）")

        if kind == "csv_equals":
            if not sb.exists(path):
                return _fail(kind, path, "文件不存在")
            rows = list(csv.reader(io.StringIO(sb.read(path))))
            rows = [r for r in rows if any(x.strip() for x in r)]
            want_rows = c.get("rows")
            if want_rows is None:
                n = c.get("row_count")
                return _ok(kind, path, f"{len(rows)} 行") if len(rows) == n else _fail(
                    kind, path, f"{len(rows)} 行，期望 {n}")
            got = [[x.strip() for x in r] for r in rows]
            want = [[str(x).strip() for x in r] for r in want_rows]
            if got == want:
                return _ok(kind, path)
            return _fail(kind, path, f"CSV 不符；实际 {got[:3]}… 期望 {want[:3]}…")

        if kind == "dir_equals":
            got = sb.listdir(c.get("path", "."))
            want = sorted(c.get("entries") or [])
            return _ok(kind, path or ".", f"{got}") if got == want else _fail(
                kind, path or ".", f"目录内容不符；实际 {got}，期望 {want}")

        if kind == "command_ok":
            cmd = str(c.get("cmd", ""))
            res = sb.run(cmd, timeout=int(c.get("timeout", 30)))
            if res.ok:
                return _ok(kind, cmd, res.brief(160).replace("\n", " | "))
            return _fail(kind, cmd, res.brief(300).replace("\n", " | "))

        if kind == "command_output_contains":
            cmd = str(c.get("cmd", ""))
            res = sb.run(cmd, timeout=int(c.get("timeout", 30)))
            needle = str(c.get("text", ""))
            blob = res.stdout + res.stderr
            if needle in blob:
                return _ok(kind, cmd, f"输出里有 {needle!r}")
            return _fail(kind, cmd, f"输出里没有 {needle!r}；实际 {blob.strip()[:160]!r}")

    except Exception as e:  # noqa: BLE001 —— 校验器自己出错也必须变成「失败」，而不是炸掉整轮
        return _fail(kind, path, f"校验器异常: {type(e).__name__}: {e}")

    return _fail(kind, path, "未处理的 check")  # pragma: no cover


def protected_violations(sb: Sandbox, protected: list[str],
                         baseline: dict[str, str]) -> list[CheckResult]:
    """被保护的文件必须原样不动 —— 专治「改测试数据让自己通过」。"""
    out: list[CheckResult] = []
    for rel in protected:
        out.append(_run_one({"kind": "file_unchanged", "path": rel}, sb, baseline))
    return out
