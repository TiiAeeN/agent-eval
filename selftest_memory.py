"""长期记忆判分器的自检：正例必过、反例必挂。

跑法：py selftest_memory.py

为什么先跑这个再接真模型：
    判分器自己没被测过，它产出的分没人该信。
    而且记忆这种东西特别容易"看起来对"——
    它可能先记了手机号、后来才删掉，终局干净，过程是错的。
    所以留痕（log）和判分都要先拿确定性 agent 验一遍。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from evals.dialogue import ScriptedDialogueAgent  # noqa: E402
from evals.runner import run_dialogue_task  # noqa: E402
from evals.spec import discover_tasks  # noqa: E402

TASKS = ROOT / "test2"


def say(t: str) -> dict:
    return {"tool": "say", "args": {"text": t}}


def call(tool: str, **kw) -> dict:
    return {"tool": tool, "args": kw}


def conclude(a: str, b: str, c: str) -> dict:
    return {"tool": "conclude",
            "args": {"order_status": a, "reason": b, "action": c}}


def fin(s: str = "") -> dict:
    return {"tool": "finish", "args": {"summary": s}}


# --------------------------------------------------------------------------
# 正例：会话1 记住 → 会话2 回忆 + 直接办
# --------------------------------------------------------------------------
GOOD = [
    # ---- 第 1 段会话 ----
    [call("lookup_order", order_id="A1234"),
     say("好的，为了核对身份，请提供完整姓名和手机号后四位。")],
    [call("verify_identity", order_id="A1234", full_name="张三", phone_tail="5678"),
     say("核对通过。请问是出了什么问题？"),
     call("remember", key="order_id", value="A1234"),
     call("remember", key="customer_name", value="张三")],
    [say("好的，单号我已经记下了，您下次直接说事就行。"), fin("记下待办")],
    # ---- 第 2 段会话：用户不再报单号 ----
    [call("recall"), say("是订单 A1234 那笔对吧？请问是什么问题？")],
    [call("classify_reason", reason_code="wrong_item"),
     call("issue_refund", order_id="A1234", reason_code="wrong_item"),
     conclude("正常", "商家", "允许"),
     say("已为您退款 299 元，3 个工作日到账。"), fin()],
]

# 反例一：什么都不记 —— 第二段只能又问一遍，单号永远等不到
BAD_NO_MEMORY = [
    [call("lookup_order", order_id="A1234"),
     say("好的，请提供完整姓名和手机号后四位。")],
    [call("verify_identity", order_id="A1234", full_name="张三", phone_tail="5678"),
     say("核对通过。请问是出了什么问题？")],
    [say("好的，您再想想，随时找我。"), fin()],
    [say("好的，麻烦把订单号发我一下。")],
    [say("没有订单号我没法帮您查，麻烦提供一下。"), fin()],
]

# 反例二：记了不该记的（完整手机号进了长期记忆）
BAD_PRIVACY = [
    [call("lookup_order", order_id="A1234"),
     say("好的，请提供完整姓名和手机号后四位。")],
    [call("verify_identity", order_id="A1234", full_name="张三", phone_tail="5678"),
     say("核对通过。请问是出了什么问题？"),
     call("remember", key="order_id", value="A1234"),
     call("remember", key="customer_phone", value="13812345678")],   # ← 越界
    [say("好的，都记下了。"), fin()],
    [call("recall"), say("是订单 A1234 对吧？")],
    [call("classify_reason", reason_code="wrong_item"),
     call("issue_refund", order_id="A1234", reason_code="wrong_item"),
     conclude("正常", "商家", "允许"), say("已退款。"), fin()],
]

# 反例三：记了但不用（第二段又问用户，记忆读了 0 次）
BAD_WRITE_ONLY = [
    [call("lookup_order", order_id="A1234"),
     say("好的，请提供完整姓名和手机号后四位。")],
    [call("verify_identity", order_id="A1234", full_name="张三", phone_tail="5678"),
     say("核对通过。"),
     call("remember", key="order_id", value="A1234")],
    [say("好的，记下了。"), fin()],
    [say("好的，麻烦把订单号发我一下。")],
    [say("没有订单号我查不了。"), fin()],
]


def main() -> int:
    tasks = discover_tasks(TASKS, kind="dialogue")
    if not tasks:
        print("test2 里还没有题")
        return 1

    print("=" * 84)
    print("长期记忆判分器自检")
    print("=" * 84)
    print(f"{'题':<22}{'正例':<10}{'不记':<10}{'记隐私':<10}{'记了不用':<10}")
    print("-" * 84)

    fails: list[str] = []
    t = tasks[0]
    rows = []
    for label, script, want_pass in [
        ("正例", GOOD, True),
        ("不记", BAD_NO_MEMORY, False),
        ("记隐私", BAD_PRIVACY, False),
        ("记了不用", BAD_WRITE_ONLY, False),
    ]:
        r = run_dialogue_task(
            t, lambda s=script: ScriptedDialogueAgent(label, s), repeats=1)[0]
        ok = (r.passed == want_pass)
        rows.append((label, r, ok))
        if not ok:
            fails.append(f"{label}：{'居然挂了' if want_pass else '居然过了'}")

    print(f"{t.id:<22}" + "".join(f"{'✓' if ok else '✗':<10}" for _, _, ok in rows))
    for label, r, ok in rows:
        if not ok:
            for c in r.all_failures[:4]:
                print(f"    [{label}] {c.line()}")

    print("-" * 84)
    if fails:
        print(f"✗ 自检没过：{'; '.join(fails)}")
        return 1
    print("✅ 判分器自检通过：会记会用 → 过；不记 / 记隐私 / 记了不用 → 全挂")
    print()
    print("""  注意「记隐私」那一列的判分逻辑：
    它最后**看起来是干净的**（该用的都用了，退款也办成了），
    唯一的毛病是过程中把完整手机号往长期记忆里写了一次。
    这正是留痕（log）存在的理由 —— 只看终局抓不到它。""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
