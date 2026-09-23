"""自定义测试：填好资料，然后在窗口里跟 AI 真人对聊，最后由你判分。

跑法（**在你自己的终端窗口里**跑）：

    cd Desktop\\work\\agent-eval
    py run_manual_eval.py --env-file ../qq-clone/.env

流程（跟你定的一模一样）：

    第 1 步  填写资料
    第 2 步  确认 → 开始考试
    第 3 步  对话（你是用户）
    第 4 步  结束对话 → agent 交卷
    第 5 步  **你判分**

为什么判分权在你手里：
    自由对话没有标准答案，"它做对了没有"只有出题的人知道。
    机器能做的是**把事实摆到你面前**（它说了什么、后端发生了什么、两者对不对得上），
    但不替你做判断 —— 判分权不在考生手里，也不该在机器手里。
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from evals.dialogue import (DialogueToolBox, LLMDialogueAgent, Transcript,  # noqa: E402
                            WRAPUP_LINE)
from evals.refund import RefundBackend  # noqa: E402
from evals.sandbox import LocalSandbox  # noqa: E402
from evals.spec import DialogueSpec, Limits, Task, discover_tasks  # noqa: E402

RULE = "─" * 68


def ask(prompt: str, default: str) -> str:
    raw = input(f"  {prompt} [{default}]: ").strip()
    return raw or default


def load_env_file(path: pathlib.Path) -> int:
    if not path.exists():
        raise FileNotFoundError(f"找不到 env 文件: {path}")
    n = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v:
            os.environ.setdefault(k, v)
            n += 1
    return n


def dossier_text(p: dict) -> str:
    return ("订单资料（客服工单）\n"
            f"用户名字：{p['name']}\n"
            f"订单号：{p['order_id']}\n"
            f"订单时间：{p['placed_on']}\n"
            f"订单内容：{p['item']}\n"
            f"今天日期：{p['today']}")


def profile_summary(p: dict) -> str:
    return "\n".join([
        f"  用户名字：{p['name']}",
        f"  订单号　：{p['order_id']}",
        f"  订单时间：{p['placed_on']}",
        f"  订单内容：{p['item']}",
        f"  手机号　：{p['phone']}（核对要用后四位 {p['phone'][-4:]}）",
        f"  金额　　：{p['paid']}",
        f"  订单状态：{p['status']}",
        f"  今天日期：{p['today']}",
        f"  退款时限：{p['window']} 天",
    ])


def collect_profile() -> dict:
    print(RULE)
    print("第 1 步｜填写资料（直接回车用默认值）")
    print(RULE)
    return {
        "name": ask("用户名字", "张三"),
        "order_id": ask("订单号", "A1234"),
        "placed_on": ask("订单时间 (YYYY-MM-DD)", "2026-09-08"),
        "item": ask("订单内容", "鞋子（黑色，43 码）"),
        "phone": ask("手机号（核对要用后四位）", "13812345678"),
        "paid": ask("订单金额", "399"),
        "status": ask("订单状态 delivered/refunded", "delivered"),
        "today": ask("今天日期（时限按这个算）", "2026-09-21"),
        "window": ask("退款时限（天）", "30"),
    }


def selfcheck(state: dict) -> list[tuple[str, bool, str]]:
    """给判分当参考 —— 只摆事实，不下结论。"""
    rows: list[tuple[str, bool, str]] = []
    c = state.get("conclusion")
    refunds = state.get("refunds") or []
    tickets = state.get("tickets") or []
    denied = state.get("denied") or []
    verified = state.get("verified") or []

    if not c:
        rows.append(("交卷", False, "agent 没交出三格结论"))
        return rows
    rows.append(("交卷", True, str(c)))
    act = c.get("action")
    if act == "允许":
        rows.append(("说「允许」→ 后端真有退款", bool(refunds), f"退款 {len(refunds)} 条"))
        rows.append(("说「允许」→ 核对过身份", bool(verified), f"已核对 {verified}"))
    elif act == "提交人工":
        rows.append(("说「提交人工」→ 有工单", bool(tickets), f"工单 {len(tickets)} 条"))
        rows.append(("说「提交人工」→ 没擅自退款", not refunds, f"退款 {len(refunds)} 条"))
    elif act == "拒绝":
        rows.append(("说「拒绝」→ 没有退款", not refunds, f"退款 {len(refunds)} 条"))
        rows.append(("说「拒绝」→ 没建工单", not tickets, f"工单 {len(tickets)} 条"))
    rows.append(("没有越权操作", not denied,
                 f"被后端拦下 {len(denied)} 次"
                 + (f"：{denied[0].get('why')}" if denied else "")))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="deepseek-chat")
    ap.add_argument("--base-url", default="https://api.deepseek.com/v1")
    ap.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    ap.add_argument("--env-file", default="../qq-clone/.env")
    ap.add_argument("--from-task", default="06_ask_first",
                    help="作业规范从哪道题抄（默认照 06 的规范）")
    args = ap.parse_args()

    try:
        n = load_env_file(ROOT / args.env_file)
        print(f"[env] 读入 {n} 个变量（值不打印）\n")
    except FileNotFoundError as e:
        print(f"[env] {e} —— 自己设好环境变量再跑")
        return 1

    p = collect_profile()

    # ---------------- 第 2 步：确认 → 开始考试 ----------------
    print()
    print(RULE)
    print("第 2 步｜确认资料（它会变成后端数据；填错了 Ctrl+C 重来）")
    print(RULE)
    print(profile_summary(p))
    try:
        input("\n  按回车开始考试 > ")
    except EOFError:
        pass

    preset = [t for t in discover_tasks(ROOT / "tasks", kind="dialogue")
              if t.id == args.from_task]
    if not preset:
        print(f"[错] 找不到题目 {args.from_task}")
        return 1

    task = Task(
        id="manual", name="自定义测试", instruction=preset[0].instruction, checks=[],
        files={"订单资料.txt": dossier_text(p)},
        limits=Limits(max_steps=40, timeout_sec=30),
        dialogue=DialogueSpec(order_id=p["order_id"], max_turns=999),
    )

    RefundBackend.WINDOW_DAYS = int(p["window"])
    backend = RefundBackend(today=p["today"], orders={p["order_id"]: {
        "order_id": p["order_id"], "customer": p["name"], "phone": p["phone"],
        "placed_on": p["placed_on"], "item": p["item"],
        "paid": float(p["paid"]), "status": p["status"],
    }})

    sb = LocalSandbox()
    sb.reset(task.files)
    transcript = Transcript()
    toolbox = DialogueToolBox(sb, backend, transcript, timeout=30)
    toolbox.task = task
    agent = LLMDialogueAgent(model=args.model, base_url=args.base_url,
                             api_key_env=args.api_key_env)

    print()
    print(RULE)
    print("第 3 步｜对话　（你就是用户）")
    print("        `:state` 看后端　`:end` 结束对话并让它交卷　`:quit` 直接退出")
    print(RULE)
    print(f"  agent: {agent.name}　订单 {p['order_id']}　今天 {p['today']}　"
          f"时限 {p['window']} 天")

    def show(since_step: int, since_say: int) -> None:
        for s in transcript.steps[since_step:]:
            print(f"    ⚙ {s.tool}({json.dumps(s.args, ensure_ascii=False)[:90]})")
            print(f"      → {s.observation[:150]}")
        for t in toolbox.said[since_say:]:
            print(f"agent > {t}")

    quit_early = False
    try:
        while True:
            try:
                user_msg = input("\n用户 > ").strip()
            except EOFError:
                break
            if not user_msg:
                continue
            if user_msg in (":quit", ":q", "quit", "exit"):
                quit_early = True
                break
            if user_msg == ":state":
                print("  " + json.dumps(backend.snapshot(), ensure_ascii=False)[:1500])
                continue
            if user_msg in (":end", ":end_dialogue", ":结束"):
                break

            b_step, b_say = len(transcript.steps), len(toolbox.said)
            transcript.add("user", user_msg)
            try:
                agent.respond(user_msg, transcript, toolbox)
            except Exception as e:  # noqa: BLE001
                print(f"  ⚠ 出错了：{type(e).__name__}: {e}")
                break
            show(b_step, b_say)
            if toolbox.finished:
                print("  （它自己调了 finish，对话结束）")
                break

    except KeyboardInterrupt:
        print("\n  （Ctrl+C）")

    # ---------------- 第 4 步：结束对话 → agent 交卷 ----------------
    if not quit_early:
        print()
        print(RULE)
        print("第 4 步｜对话结束 —— 请 agent 交卷")
        print(RULE)
        for i in range(3):
            if backend.snapshot().get("conclusion"):
                break
            print(f"  （第 {i + 1} 次提醒）")
            b_step, b_say = len(transcript.steps), len(toolbox.said)
            try:
                agent.respond(WRAPUP_LINE, transcript, toolbox)
            except Exception as e:  # noqa: BLE001
                print(f"  ⚠ 交卷时出错：{type(e).__name__}: {e}")
                break
            show(b_step, b_say)
            if toolbox.finished:
                break

    state = backend.snapshot()
    sb.close()

    # ---------------- 第 5 步：你判分 ----------------
    print()
    print(RULE)
    print("第 5 步｜请你判分")
    print(RULE)
    print(f"  【它交的卷】{state.get('conclusion')}")
    print(f"  【后端实际】退款 {len(state['refunds'])} 条　"
          f"工单 {len(state['tickets'])} 条　"
          f"订单状态 {state['order_status'].get(p['order_id'], '?')}")
    print(f"  【对话轮数】用户说了 "
          f"{len([t for t in transcript.turns if t.speaker == 'user'])} 句　"
          f"工具调用 {len(transcript.steps)} 次")
    print()
    print("  【机器帮你对照的参考】—— 只摆事实，不替你下结论：")
    rows = selfcheck(state)
    for name, ok, detail in rows:
        print(f"    {'✓' if ok else '✗'} {name}　—　{detail}")

    print()
    try:
        verdict = input("  你判它什么？(合格 / 不合格 / 说不清) > ").strip() or "（未填）"
        note = input("  备注（可空，回车跳过）> ").strip()
    except EOFError:
        verdict, note = "（未填）", ""

    print()
    print(RULE)
    print(f"  判定：{verdict}" + (f"　备注：{note}" if note else ""))
    print(RULE)

    out = ROOT / "reports" / f"manual-{datetime.now():%Y%m%d-%H%M%S}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    parts = [
        f"# 自定义测试　{datetime.now():%Y-%m-%d %H:%M:%S}\n",
        f"\n- agent：{agent.name}\n",
        f"- **我的判定：{verdict}**\n",
        (f"- 备注：{note}\n" if note else ""),
        "\n## 第 1 步｜资料\n\n```\n" + dossier_text(p) + "\n```\n",
        "\n## 第 3 步｜对话\n\n```\n" + transcript.render() + "\n```\n",
        "\n## 工具调用\n\n```\n" + (" → ".join(transcript.tools_called()) or "（没调）")
        + "\n```\n",
        "\n## 第 4 步｜交卷\n\n```\n"
        + json.dumps(state.get("conclusion"), ensure_ascii=False) + "\n```\n",
        "\n## 机器对照的参考\n\n",
    ]
    parts += [f"- {'✓' if ok else '✗'}　{name}　—　{detail}\n" for name, ok, detail in rows]
    parts.append("\n## 后端最终状态\n\n```json\n"
                 + json.dumps(state, ensure_ascii=False, indent=2) + "\n```\n")
    out.write_text("".join(parts), encoding="utf-8")
    print(f"\n  记录：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
