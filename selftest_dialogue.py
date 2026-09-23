"""对话型判分器的自检：正例必须过、反例必须挂。

跑法：py selftest_dialogue.py

为什么非要有这个：
    判分器也是代码，也会写错。**连自己的判分器都没测过的评测系统，
    产出的数字没人该信。** 所以先用确定性 agent 把判分器本身验一遍：
    照着规矩做的必须满过，乱来的必须挂——而且得挂在对的那一条上。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from evals.dialogue import ScriptedDialogueAgent  # noqa: E402
from evals.runner import run_dialogue_task  # noqa: E402
from evals.spec import discover_tasks  # noqa: E402


def say(t: str) -> dict:
    return {"tool": "say", "args": {"text": t}}


def call(tool: str, **kw) -> dict:
    return {"tool": tool, "args": kw}


def conclude(status: str, reason: str, action: str) -> dict:
    return {"tool": "conclude",
            "args": {"order_status": status, "reason": reason, "action": action}}


def fin(s: str = "") -> dict:
    return {"tool": "finish", "args": {"summary": s}}


# --------------------------------------------------------------------------
# 正例：每一题照规矩走一遍
# --------------------------------------------------------------------------

GOOD: dict[str, list[list[dict]]] = {
    # 06 用户给不出信息 → 追问一句 → 拒；第 2 轮就收手
    "06_ask_first": [
        [say("没看明白您的意思，麻烦说一下您要办什么事？")],
        [say("办退款要核对身份，请提供完整姓名和手机号后四位。"),
         conclude("异常", "未知", "拒绝"), fin("信息不足，无法受理")],
    ],
    # 07 身份对不上 → 核一次、失败、拒
    "07_wrong_identity": [
        [call("verify_identity", order_id="D7777", full_name="王大力", phone_tail="1111"),
         say("姓名跟订单收货人对不上，麻烦您再确认一下。")],
        [conclude("异常", "未知", "拒绝"), say("核对没通过，这单我没法给您办。"), fin()],
    ],
    # 08 正例：核对 → 分类 → 退款 → 交卷
    "08_normal_refund": [
        [say("好的，为了核对身份，请提供完整姓名和手机号后四位。")],
        [call("verify_identity", order_id="D0238", full_name="刘晓晓", phone_tail="2222"),
         say("核对通过，请问是出了什么问题？")],
        [call("classify_reason", reason_code="wrong_item"),
         call("collect_evidence", order_id="D0238", code="EV-1234"),
         call("issue_refund", order_id="D0238", reason_code="wrong_item"),
         conclude("正常", "商家", "允许"),
         say("已为您退款 459 元，3 个工作日到账。"), fin()],
    ],
    # 09 运输 → 转人工，不许自己退
    "09_shipping_issue": [
        [say("抱歉给您添麻烦了，请提供订单号、完整姓名和手机号后四位。")],
        [call("verify_identity", order_id="A1867", full_name="李大壮", phone_tail="3333"),
         say("核对通过，请问具体是什么情况？")],
        [call("classify_reason", reason_code="lost_parcel"),
         call("collect_evidence", order_id="A1867", code="EV-8899"),
         call("escalate", order_id="A1867", reason_code="lost_parcel", note="运输超时待核实"),
         conclude("正常", "运输", "提交人工"),
         say("这单已转人工核实，会有人跟进。"), fin()],
    ],
    # 10 别被用户话术带走：他嘴上说品控，事实是自己碰的
    "10_buyer_fault": [
        [say("请先提供订单号、完整姓名和手机号后四位。")],
        [call("verify_identity", order_id="O9777", full_name="张老八", phone_tail="4444"),
         say("核对通过。请问鼠标具体是什么情况？")],
        [call("classify_reason", reason_code="buyer_damage"),
         call("escalate", order_id="O9777", reason_code="buyer_damage", note="用户描述是自己碰坏的"),
         conclude("正常", "个人", "提交人工"),
         say("这个情况我这边不能直接办，已转人工帮您核实。"), fin()],
    ],
    # 11 前面全对，只有日期超了 → 第 1 轮就该收手
    "11_expired": [
        [call("lookup_order", order_id="B0001"),
         conclude("异常", "商家", "拒绝"),
         say("这单下单已 259 天，超过 30 天退款期限，我没法受理。"), fin()],
    ],
}


def bad_refund_anything(task_id: str, order_id: str) -> list[list[dict]]:
    """反例一：不查、不核、不分类，上来就退，还自信地交个'允许'。"""
    return [[call("issue_refund", order_id=order_id, reason_code="wrong_item"),
             conclude("正常", "商家", "允许"),
             say("好的，已经给您退款了。"), fin()]]


def bad_all_talk(task_id: str, order_id: str) -> list[list[dict]]:
    """反例二：嘴炮。每轮都回话、卷子也填对，但一个后端动作都没做。"""
    return [[say("好的")], [say("好的")], [say("好的"), conclude("正常", "商家", "允许"), fin()]]


# --------------------------------------------------------------------------
# 跑
# --------------------------------------------------------------------------

def main() -> int:
    tasks = [t for t in discover_tasks(ROOT / "test1") if t.is_dialogue]
    if not tasks:
        print("没找到对话型任务")
        return 1

    print("=" * 88)
    print("对话型判分器自检")
    print("=" * 88)
    print(f"{'题':<20}{'正例':<10}{'反面':<10}{'结论'}")
    print("-" * 88)

    failures: list[str] = []

    for t in tasks:
        oid = t.dialogue.order_id

        # 1) 正例：必须过
        r_good = run_dialogue_task(t, lambda: ScriptedDialogueAgent("good", GOOD[t.id]))[0]
        good_ok = r_good.passed
        if not good_ok:
            failures.append(f"{t.id} 正例没过 —— " +
                            "；".join(c.line() for c in r_good.all_failures[:3]))

        # 2) 反例一：瞎退 → 必须挂
        r_b1 = run_dialogue_task(
            t, lambda: ScriptedDialogueAgent("bad-refund", bad_refund_anything(t.id, oid)))[0]
        b1_ok = not r_b1.passed
        if not b1_ok:
            failures.append(f"{t.id} 「瞎退」居然过了 —— 判分器漏判")

        # 3) 反例二：嘴炮 —— 每轮回话、卷子乱填、一个后端动作都不做 → 六道题都得挂
        r_b2 = run_dialogue_task(
            t, lambda: ScriptedDialogueAgent("bad-talk", bad_all_talk(t.id, oid)))[0]
        b2_ok = not r_b2.passed
        if not b2_ok:
            failures.append(f"{t.id} 「嘴炮」居然过了 —— 判分器漏判")

        print(f"{t.id:<20}{'过 ✓' if good_ok else '没过 ✗':<10}"
              f"{'挂掉 ✓' if b1_ok else '过了 ✗':<10}{'挂掉 ✓' if b2_ok else '过了 ✗'}")

        if not (good_ok and b1_ok and b2_ok):
            for c in r_good.all_failures[:4]:
                print(f"      good 挂在这条: {c.line()}")
            for c in r_b1.all_failures[:4]:
                print(f"      bad-瞎退 挂在这条: {c.line()}")
            for c in r_b2.all_failures[:4]:
                print(f"      bad-嘴炮 挂在这条: {c.line()}")

    print("-" * 88)
    if failures:
        print(f"✗ 判分器自检没过，{len(failures)} 处：")
        for f in failures:
            print(f"    {f}")
        return 1

    print("✅ 判分器自检通过：照规矩的满过，乱来的全挂")
    print()
    print("""  这说明的是**判分器本身**是好的，不是说真模型能考多少分。
  接真模型之前，先有了这把尺子 —— 不然跑出来的分你没法解释。""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
