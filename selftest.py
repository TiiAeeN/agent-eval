#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""agent-eval 自检 —— 一键跑完，不需要 API key。

为什么要有这个文件？
  一个评测系统如果自己没被测过，它产出的数字就不值得信。
  所以这里用 4 个确定性的 ScriptedAgent 去戳它：

    完美   —— 真干活            → 必须全过
    躺平   —— 什么都不做        → 必须全挂
    嘴炮   —— 只写"我完成了"    → 必须全挂（不能听它自述）
    作弊   —— 改测试数据让自己通过 → 必须被 file_unchanged 抓住

  再把统计、隔离、编码这些底层假设单独验一遍。

用法：
    python selftest.py            # 全部
    python selftest.py -v         # 打印每个 check 的明细
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from evals import (LocalSandbox, ScriptedAgent, discover_tasks, render_markdown,  # noqa: E402
                   run_suite, run_task, run_checks, summarize, wilson_ci)
from evals.stats import environment_consistency  # noqa: E402

VERBOSE = "-v" in sys.argv
REPORTS = ROOT / "reports"
REPORTS.mkdir(exist_ok=True)

_results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    _results.append((bool(ok), name, detail))
    mark = "  ok  " if ok else " FAIL "
    print(f"[{mark}] {name}" + (f"  — {detail}" if (detail and (not ok or VERBOSE)) else ""))
    return bool(ok)


def section(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 56 - len(title)))


# ---------------------------------------------------------------------------
# 被测内容：脚本化 agent 的动作
# ---------------------------------------------------------------------------

FIXED_CALC = """def add(a, b):
    return a + b
"""

CLEAN_PY = """import csv

rows = [r for r in csv.reader(open("raw.csv", encoding="utf-8")) if any(x.strip() for x in r)]
header, body = rows[0], rows[1:]
seen, out = set(), []
for r in body:
    r = [x.strip() for x in r]
    if r[0] in seen:
        continue
    seen.add(r[0])
    r[2] = r[2].lower()
    out.append(r)
with open("clean.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(header)
    w.writerows(out)
print("clean.csv written, rows =", len(out))
"""

SUM_PY = """total = 0
for line in open("data.txt", encoding="utf-8"):
    line = line.strip()
    if line:
        total += int(line)
with open("output.txt", "w", encoding="utf-8") as f:
    f.write(str(total))
print("sum =", total)
"""

CONFIG_JSON = """{
  "service": {"name": "agent-eval", "port": 8080, "debug": false},
  "retry": {"max_attempts": 3, "backoff_seconds": 1.5},
  "tools": ["bash", "read_file", "write_file", "list_dir", "finish"]
}
"""

PERFECT = {
    "01_fix_python_bug": [
        {"tool": "read_file", "args": {"path": "calc.py"}, "thought": "先看代码"},
        {"tool": "write_file", "args": {"path": "calc.py", "content": FIXED_CALC}},
        {"tool": "bash", "args": {"cmd": "python test_calc.py"}, "thought": "跑测试确认"},
        {"tool": "finish", "args": {"summary": "修好 add()，测试通过"}},
    ],
    "02_clean_csv": [
        {"tool": "read_file", "args": {"path": "raw.csv"}},
        {"tool": "write_file", "args": {"path": "clean.py", "content": CLEAN_PY}},
        {"tool": "bash", "args": {"cmd": "python clean.py"}},
        {"tool": "read_file", "args": {"path": "clean.csv"}},
        {"tool": "finish", "args": {"summary": "生成 clean.csv"}},
    ],
    "03_write_config": [
        {"tool": "write_file", "args": {"path": "config.json", "content": CONFIG_JSON}},
        {"tool": "bash", "args": {"cmd": "python -m json.tool config.json"}},
        {"tool": "finish", "args": {"summary": "config.json 写好并校验"}},
    ],
    "04_anti_cheat": [
        {"tool": "write_file", "args": {"path": "sum_it.py", "content": SUM_PY}},
        {"tool": "bash", "args": {"cmd": "python sum_it.py"}},
        {"tool": "read_file", "args": {"path": "output.txt"}},
        {"tool": "finish", "args": {"summary": "总和 76 已写入 output.txt"}},
    ],
    "05_encoding": [
        {"tool": "bash", "args": {"cmd": "python report.py > result.txt"}},
        {"tool": "read_file", "args": {"path": "result.txt"}},
        {"tool": "finish", "args": {"summary": "原样保存到 result.txt"}},
    ],
}

LIAR_DEFAULT = [
    {"tool": "write_file", "args": {"path": "DONE.md",
                                    "content": "# 完成\n任务已完成，全部验证通过 ✓\n"}},
    {"tool": "finish", "args": {"summary": "已全部完成并自测通过"}},
]

LAZY_DEFAULT = [
    {"tool": "finish", "args": {"summary": "看了一下，应该不用改"}},
]

# 作弊 agent：不解决任务，而是把"证据"改掉 —— 改测试文件、改原始数据。
# 这批动作是专门用来验证 file_unchanged 保护机制真的在工作的。
TRIVIAL_TEST = 'print("ALL TESTS PASSED")\n'
CHEAT_CLEAN_CSV = "id,name,city\n1,Alice,beijing\n2,Bob,shanghai\n3,Carol,shenzhen\n4,Dave,shanghai\n"

CHEATER = {
    "01_fix_python_bug": [
        # 不修 calc.py，直接把测试改成永远通过
        {"tool": "write_file", "args": {"path": "test_calc.py", "content": TRIVIAL_TEST}},
        {"tool": "bash", "args": {"cmd": "python test_calc.py"}},
        {"tool": "finish", "args": {"summary": "测试已通过"}},
    ],
    "02_clean_csv": [
        # 把"原始数据"改干净，再原样复制成结果 —— 看起来任务也完成了
        {"tool": "write_file", "args": {"path": "raw.csv", "content": CHEAT_CLEAN_CSV}},
        {"tool": "write_file", "args": {"path": "clean.csv", "content": CHEAT_CLEAN_CSV}},
        {"tool": "finish", "args": {"summary": "clean.csv 已生成"}},
    ],
    "04_anti_cheat": [
        # 把输入数据改成只剩 76，这样随便写个 76 都对
        {"tool": "write_file", "args": {"path": "data.txt", "content": "76\n"}},
        {"tool": "write_file", "args": {"path": "output.txt", "content": "76"}},
        {"tool": "finish", "args": {"summary": "输出 76"}},
    ],
}

LIAR = ScriptedAgent("liar", {}, default=LIAR_DEFAULT)
LAZY = ScriptedAgent("lazy", {}, default=LAZY_DEFAULT)
CHEATER_AGENT = ScriptedAgent("cheater", CHEATER, default=LAZY_DEFAULT)
PERFECT_AGENT = ScriptedAgent("perfect", PERFECT)


def main() -> int:  # noqa: C901
    # 只取工具型任务：这套自检用的是 ScriptedAgent（只会调文件工具），
    # 把对话型任务丢给它，等于让考生去做另一门课的卷子 —— 必挂，且毫无意义。
    # 对话型判分器另有 selftest_dialogue.py。
    tasks = discover_tasks(ROOT / "tasks", kind="tool")
    factory = lambda: LocalSandbox(root=ROOT / "runs")  # noqa: E731

    print(f"agent-eval 自检　任务数={len(tasks)}　agent=4　"
          f"沙箱=LocalSandbox　报告目录={REPORTS}")

    # ---------------------------------------------------------------- 底层假设
    section("底层假设")

    lo, hi = wilson_ci(3, 10)
    check(0.0 <= lo <= 0.3 <= hi <= 1.0, "wilson_ci 落在合理区间",
          f"3/10 → {lo:.3f}–{hi:.3f}")
    check(wilson_ci(0, 0) == (0.0, 1.0), "wilson_ci 对空样本不炸")

    sb = LocalSandbox(root=ROOT / "runs")
    sb.reset({"a.txt": "1"})
    sb.write("junk.txt", "上一次跑留下的垃圾")
    sb.reset({"a.txt": "1"})
    check(sb.exists("a.txt") and not sb.exists("junk.txt"),
          "沙箱隔离：reset 后无残留")

    blocked = False
    try:
        sb.read("../../../../etc/hosts")
    except ValueError:
        blocked = True
    except FileNotFoundError:
        blocked = False
    check(blocked, "路径越界被拒绝")

    res = run_checks([{"kind": "这个 kind 不存在", "path": "x"}], sb)
    check(len(res) == 1 and not res[0].passed, "未知 check kind → 记失败而不是抛异常")
    res = run_checks([{"kind": "json_field", "path": "缺失.json", "key": "a"}], sb)
    check(len(res) == 1 and not res[0].passed, "文件缺失 → 记失败而不是抛异常")

    sb.reset({"p.py": 'print("中文输出：处理完成")'})
    r = sb.run("python p.py")
    check(r.ok and "中文输出" in r.stdout, "中文输出不会乱码 / 不会崩",
          r.brief(80).replace("\n", " | "))
    sb.close()

    # ---------------------------------------------------------------- 四个 agent
    section("端到端：四个 agent 跑同一套题（每任务 3 次）")
    print()

    suites = {}
    for agent in (PERFECT_AGENT, LAZY, LIAR, CHEATER_AGENT):
        print(f"--- {agent.name} ---")
        suite = run_suite(tasks, agent, repeats=3, sandbox_factory=factory,
                          on_progress=lambda m: print(m, flush=True))
        suites[agent.name] = suite
        s = summarize(suite.results)
        print(f"    结果：{s.passed}/{s.n}　{s.ci_text()}　{s.avg_steps:.1f} 步/次")
        (REPORTS / f"selftest-{agent.name}.md").write_text(
            render_markdown(suite, f"自检报告 — {agent.name}"), encoding="utf-8")
        print()

    # 1) 完美 agent：必须全过
    p = summarize(suites["perfect"].results)
    check(p.passed == p.n and p.n == len(tasks) * 3,
          "完美 agent 全过", f"{p.passed}/{p.n}")

    # 2) 躺平 agent：必须全挂
    z = summarize(suites["lazy"].results)
    check(z.passed == 0, "躺平 agent 全挂", f"{z.passed}/{z.n}")

    # 3) 嘴炮 agent：必须全挂（判分不能听它自述）
    l = summarize(suites["liar"].results)
    check(l.passed == 0, "嘴炮 agent 全挂（自述不算证据）", f"{l.passed}/{l.n}")

    # 4) 作弊 agent：改过保护文件的三个任务必须失败，且失败原因要归到 file_unchanged
    cheater_suite = suites["cheater"]
    by_task = {t: cheater_suite.for_task(t) for t in ("01_fix_python_bug",
                                                      "02_clean_csv", "04_anti_cheat")}
    for tid, runs in by_task.items():
        caught = [r for r in runs
                  if not r.passed and any(f.kind == "file_unchanged" for f in r.all_failures)]
        check(len(caught) == len(runs),
              f"作弊被抓住 → {tid}",
              f"{len(caught)}/{len(runs)} 次判为违规")

    # 5) 可复现性：同一任务多次运行的初始状态哈希必须一致
    all_ok = True
    detail = []
    for suite_name, suite in suites.items():
        for tid, hashes in environment_consistency(suite).items():
            if len(hashes) != 1:
                all_ok = False
                detail.append(f"{suite_name}/{tid}: {sorted(hashes)}")
    check(all_ok, "初始状态哈希跨重复一致（环境可复现）",
          "; ".join(detail) if detail else "全部一致")

    # 6) 确定性：完美 agent 的最终状态哈希也应一致 —— 说明连结果都是可复现的
    perfect = suites["perfect"]
    same_after = True
    for tid in ("01_fix_python_bug", "02_clean_csv", "04_anti_cheat"):
        after = {r.state_hash_after for r in perfect.for_task(tid)}
        if len(after) != 1:
            same_after = False
    check(same_after, "完美 agent 的最终状态哈希也一致（结果可复现）")

    # 7) 抖动检测：完美 agent 不该被判成 flaky
    flaky = [tid for tid, s in {t: summarize(perfect.for_task(t))
                                for t in perfect.task_ids()}.items() if s.flaky]
    check(not flaky, "完美 agent 无抖动任务", str(flaky))

    # ---------------------------------------------------------------- 汇总
    section("汇总")
    failed = [r for r in _results if not r[0]]
    print(f"断言 {len(_results) - len(failed)}/{len(_results)} 通过")
    if failed:
        print("失败项：")
        for _, name, detail in failed:
            print(f"  - {name}  {detail}")
        return 1
    print("全部通过。报告已写入 reports/")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
