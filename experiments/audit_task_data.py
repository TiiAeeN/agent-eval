"""题目自洽性检查：把每道题用到的订单数据，跟后端数据、预期答案三方对一遍。

跑法：py experiments/audit_task_data.py

为什么要有这个东西：
    题目里的单号、姓名、下单时间，必须跟后端数据和预期答案**自洽**。
    不自洽的题不会报错 —— 它只会**静默地把好 agent 判成坏 agent**。
    一份跑得通、但题目本身矛盾的卷子，产出的分数是假的。

表里最后一列「判异常的依据」是必须显式写出来的：
    「订单状况=异常」可以由三件事导致 —— 日期超期 / 状态已退款 / 身份核对不过。
    写清楚是哪一条，这个脚本才能替你验；不写，就只能靠人看。
"""
import pathlib
import sys
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.refund import ORDERS, TODAY, RefundBackend  # noqa: E402

WINDOW = RefundBackend.WINDOW_DAYS
TODAY_D = date.fromisoformat(TODAY)

# 题目, 单号, 资料里的收货人, 资料里的下单时间, 预期状况, 预期原因, 预期情况, 判异常的依据
CASES = [
    ("06", "A1234", "张三",   "2026-09-08", "异常", "未知", "拒绝",     "身份"),
    ("07", "D7777", "王小明", "2026-09-05", "异常", "未知", "拒绝",     "身份"),
    ("08", "D0238", "刘晓晓", "2026-09-01", "正常", "商家", "允许",     ""),
    ("09", "A1867", "李大壮", "2026-08-30", "正常", "运输", "提交人工", ""),
    ("10", "O9777", "张老八", "2026-09-18", "正常", "个人", "提交人工", ""),
    ("11", "B0001", "李四",   "2026-01-05", "异常", "商家", "拒绝",     "日期"),
]

problems: list[str] = []
manual: list[str] = []


def bad(case: str, msg: str) -> None:
    problems.append(f"{case}  {msg}")


print("=" * 92)
print(f"题目自洽性检查   今天={TODAY}   退款窗口={WINDOW} 天")
print("=" * 92)
print(f"{'题':<4}{'单号':<8}{'资料收货人':<12}{'资料下单':<12}{'距今':>5}  "
      f"{'后端状态':<10}{'窗口内':<7}{'依据':<6}{'预期':<6}")
print("-" * 92)

for cid, oid, name, placed, w_stat, w_reason, w_act, why in CASES:
    o = ORDERS.get(oid)
    if o is None:
        bad(cid, f"单号 {oid} 在后端里根本不存在")
        print(f"{cid:<4}{oid:<8}{name:<12}{placed:<12}{'—':>5}  {'不存在':<10}{'—':<7}{why:<6}{w_stat:<6}")
        continue

    days = (TODAY_D - date.fromisoformat(o["placed_on"])).days
    in_window = days <= WINDOW
    refunded = o["status"] == "refunded"

    print(f"{cid:<4}{oid:<8}{o['customer']:<12}{o['placed_on']:<12}{days:>5}  "
          f"{o['status']:<10}{'是' if in_window else '否':<7}{why or '—':<6}{w_stat:<6}")

    # 1) 资料的收货人 / 下单时间，必须跟后端一致
    if o["customer"] != name:
        bad(cid, f"资料的收货人写「{name}」，后端是「{o['customer']}」")
    if o["placed_on"] != placed:
        bad(cid, f"资料的下单时间写 {placed}，后端是 {o['placed_on']}")

    # 2) 预期「正常」：三条客观条件必须全过，而且不该有"判异常的依据"
    if w_stat == "正常":
        if not in_window:
            bad(cid, f"预期「正常」，但下单 {days} 天已超期（窗口 {WINDOW} 天）")
        if refunded:
            bad(cid, "预期「正常」，但后端状态是 refunded（已退过款）")
        if why:
            bad(cid, f"预期「正常」，却填了「判异常的依据={why}」——两者矛盾")
    # 3) 预期「异常」：必须说清楚靠什么判异常，而且该依据得真的成立
    else:
        if not why:
            bad(cid, "预期「异常」，但没写「判异常的依据」是哪一条")
        elif why == "日期":
            if in_window:
                bad(cid, f"依据写「日期」，可下单才 {days} 天，没超期")
        elif why == "状态":
            if not refunded:
                bad(cid, f"依据写「状态」，可后端状态是 {o['status']}，不是 refunded")
        elif why == "身份":
            manual.append(f"{cid}  靠「身份核对失败」判异常 —— 这层查不了，"
                          f"得人看剧本：用户到底有没有给出对不上的信息")
        else:
            bad(cid, f"「判异常的依据」只能是 日期/状态/身份，写的是「{why}」")

    # 4) 规则：只有「商家」才能"允许"；运输/个人一律转人工
    if w_act == "允许" and w_reason != "商家":
        bad(cid, f"预期「允许」，但原因是「{w_reason}」（只有商家能直接退）")
    if w_reason in ("运输", "个人") and w_act != "提交人工":
        bad(cid, f"原因是「{w_reason}」，预期情况却是「{w_act}」（应该 提交人工）")

print("-" * 92)
unused = sorted(set(ORDERS) - {c[1] for c in CASES})
print(f"后端里没有题目用到的单号：{unused or '（无）'}")
print()

print("=" * 92)
if problems:
    print(f"✗ 发现 {len(problems)} 处不自洽：")
    for p in problems:
        print(f"    {p}")
else:
    print("✅ 数据层全部自洽")

if manual:
    print()
    print("⚠ 数据层查不了、得人看的（这些不算错）：")
    for m in manual:
        print(f"    {m}")

print()
print("""数据层之外还有一层，脚本永远查不了 —— **用户剧本的台词够不够 agent 完成核对**。
用户只报姓名、不报手机尾号，agent 就过不了 verify_identity，
那预期答案写「允许」就落不了地。这一层只能靠人通读剧本。""")
print("=" * 92)
