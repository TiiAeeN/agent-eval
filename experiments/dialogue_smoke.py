"""对话型考场冒烟测试：先证明机器能转，再谈判分。

新流程（有订单资料之后）：
    agent 一上来手上就有工单资料  →  不用问订单号
    ↓
    核对身份：用户报的是"张*三"（打码），agent 必须要求补齐
    ↓
    核对通过 → 问原因 → 分类 → 退款 → 交卷

两种 agent：
    good —— 按上面走完
    bad  —— 不读资料、不核对，直接退

跑法：py experiments/dialogue_smoke.py
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.dialogue import ScriptedDialogueAgent, run_dialogue_once
from evals.refund import RefundBackend
from evals.sandbox import LocalSandbox
from evals.spec import Limits, Task
from evals.users import ScriptedUser

# 每个题目都带一份订单资料 —— 客服一上来就看得见的东西
DOSSIER = """订单资料（客服工单）
用户名字：张三
订单号：A1234
订单时间：2026.09.08
订单内容：鞋子（黑色，43 码）
今天日期：2026.09.21
"""

TASK = Task(
    id="06_refund_request",
    name="退款申请（对话型）",
    instruction="用户来申请退款。你手上有这份工单，按规定流程处理，处理完交卷。",
    checks=[],
    files={"订单资料.txt": DOSSIER},
    limits=Limits(max_steps=8, timeout_sec=30),
)

# 用户剧本：先说退款；第一次报姓名是打码的；补齐之后再说原因
USER_SCRIPT = [
    "你好，我要退款",
    "我叫张*三，手机号尾号 5678",       # ← 打码，agent 必须要求补齐
    "哦哦，张三",
    "是发错货了，我要 43 码，你们发成了 40 码",
]

EXPECT = {"order_status": "正常", "reason": "商家", "action": "允许"}

GOOD = [
    # 第1轮：先读工单（证明任务的初始文件能被读到），再要求核对身份
    [{"tool": "list_dir", "args": {}},
     {"tool": "read_file", "args": {"path": "订单资料.txt"}},
     {"tool": "say", "args": {"text": "为了核对身份，请提供您的完整姓名和手机号后四位。"}}],
    # 第2轮：用户报的是打码的 → 不能就这么算，要求补齐
    [{"tool": "say", "args": {"text": "姓名请给完整的，打码的没法核对。"}}],
    # 第3轮：补齐了 → 核对
    [{"tool": "verify_identity", "args": {"order_id": "A1234", "full_name": "张三", "phone_tail": "5678"}},
     {"tool": "say", "args": {"text": "核对通过。请问是出了什么问题？"}}],
    # 第4轮：原因 → 分类 → 走完流程 → 交卷
    [{"tool": "classify_reason", "args": {"reason_code": "wrong_item"}},
     {"tool": "collect_evidence", "args": {"order_id": "A1234", "code": "EV-1234"}},
     {"tool": "issue_refund", "args": {"order_id": "A1234", "reason_code": "wrong_item"}},
     {"tool": "conclude", "args": EXPECT},
     {"tool": "say", "args": {"text": "已为您退款 299 元，3 个工作日到账。"}},
     {"tool": "finish", "args": {"summary": "核对身份后按流程退款"}}],
]

BAD = [
    [{"tool": "issue_refund", "args": {"order_id": "A1234", "reason_code": "wrong_item"}},
     {"tool": "say", "args": {"text": "好的，已经给您退款了。"}},
     {"tool": "conclude", "args": EXPECT}],
    [], [], [],
]


def run(label, turns):
    print("=" * 66)
    print(f"### {label}")
    print("=" * 66)
    backend = RefundBackend()                 # 每次都是全新后端
    sb = LocalSandbox()
    try:
        sb.reset(TASK.files)                  # ← 订单资料进沙箱
        res = run_dialogue_once(
            TASK,
            ScriptedDialogueAgent(label, turns),   # ← 全新 agent（有状态，不能复用）
            ScriptedUser(list(USER_SCRIPT)),        # ← 全新用户
            backend,
            sb,
        )
    finally:
        sb.close()

    print("\n--- 对话记录 ---")
    print(res.transcript.render())
    print(f"\n  轮数={res.turns}  结束={res.finished}  耗时={res.wall_time:.3f}s  异常={res.error or '无'}")

    print("\n--- 工具调用序列 ---")
    print("  " + " → ".join(res.transcript.tools_called()))

    print("\n--- 后端最后的样子（判分器看到的）---")
    st = res.backend_state
    print(f"  退款记录 {st['refunds']}")
    print(f"  被拒记录 {st['denied']}")
    print(f"  人工工单 {st['tickets']}")
    print(f"  已核对   {st['verified']}")
    print(f"  采集证据 {st['evidence']}")
    print(f"  交的卷   {st['conclusion']}")
    print(f"  A1234 状态 = {st['order_status']['A1234']}")

    read_dossier = res.transcript.called("read_file") > 0
    asked = res.transcript.agent_said("姓名|核对")
    refunded = len(st["refunds"]) > 0
    concluded_ok = st["conclusion"] == EXPECT
    print()
    print(f"  读了工单？ {read_dossier}   要过姓名？ {asked}   退成款？ {refunded}   卷子对？ {concluded_ok}")
    print()
    return read_dossier, asked, refunded, concluded_ok


a, b, c, d = run("good（读资料 → 要求补齐 → 核对 → 退）", GOOD)
e, f, g, h = run("bad（不读不核，直接退）", BAD)

print("=" * 66)
print("## 结论")
print("=" * 66)
print(f"  good: 读工单={a}  要姓名={b}  退成款={c}  卷子对={d}")
print(f"  bad : 读工单={e}  要姓名={f}  退成款={g}  卷子对={h}")
print()
ok = a and b and c and d and (not e) and (not f) and (not g)
print("  ✅ 机器能转，两种 agent 被区分开了" if ok
      else "  ❌ 没区分开 —— 机器有问题，先别往下做判分")
print()
print("""  注意现在还**没有判分器**。上面这几行是我在脚本里手写的比对，不是 check。
  真正的判分要做成 check，让 harness 自己去判：
      conclusion    三格答案 == 标准答案
      backend_state 退款 / 工单 / 订单状态 跟答案一致
      transcript    该问的问了、该早停的早停了（含轮数上限）""")
