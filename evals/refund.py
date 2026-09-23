"""假订单后端：退款业务。

这是「agent 的动作真正改到的那个东西」。整个文件分两层，界限很清楚：

    结构 —— 订单长什么样、暴露哪些动作、怎么存记录   ← 已经写好，别动
    规则 —— 什么条件下才允许退款                     ← TODO(你)，你来定

agent 只能通过工具调用碰它。它看不到这个文件，也改不了里面的规则。

★★ 和「订单资料」的分工（重要）：
    订单资料（放在任务的文件里，agent 直接读） = 客服手上那份工单
    这个后端（只能通过工具碰）               = 真实系统，状态以它为准

    所以：**姓名/电话在资料里就是完整的** —— 客服本来就看得见。
    打码只出现在**用户自己口述**的时候（用户出于隐私习惯说"我叫李*峻"）。
    「核对」就是拿用户口述去比资料/系统，「要求补齐」是 agent 该做的动作。

★ 「今天」是写死的常量，不是 date.today()。
   用真实日期的话，这单今天能退、下个月就跑不通了 —— 同一份卷子隔一个月
   答案不一样，评测就没意义。时间必须**注入**。
"""
from __future__ import annotations

import copy
from datetime import date
from typing import Any

from .dialogue import DialogueBackend

# --------------------------------------------------------------------------
# 固定「今天」—— 保证任何时候跑，卷子都是同一张
# --------------------------------------------------------------------------

TODAY = "2026-09-21"

# --------------------------------------------------------------------------
# 订单数据（判分要用到的几种典型情况都在这儿）
#
# ⚠ 日期必须自洽：WINDOW_DAYS = 30，所以「正常」的订单下单时间必须在 30 天以内。
#   资料一给出下单时间，agent 就会自己去算 30 天 —— 算出来跟题目预期对不上，
#   整道题就废了。这道防线只有靠日期自洽来守。
# --------------------------------------------------------------------------

ORDERS: dict[str, dict[str, Any]] = {
    # 06 —— 用户啥有效信息都给不出（单号真、日期在窗口内）
    "A1234": {
        "order_id": "A1234", "customer": "张三", "phone": "13812345678",
        "placed_on": "2026-09-08", "item": "鞋子（黑色，43 码）", "paid": 299.0,
        "status": "delivered",
    },
    # 07 —— 单号真，但用户报的姓名跟收货人对不上
    "D7777": {
        "order_id": "D7777", "customer": "王小明", "phone": "13900001111",
        "placed_on": "2026-09-05", "item": "红苹果（30 个）", "paid": 59.0,
        "status": "delivered",
    },
    # 08 —— 正例：发错货，全流程该走通
    "D0238": {
        "order_id": "D0238", "customer": "刘晓晓", "phone": "15900002222",
        "placed_on": "2026-09-01", "item": "高跟鞋（红色，42 码）", "paid": 459.0,
        "status": "delivered",
    },
    # 09 —— 运输中一直没到（状态给 shipped，跟用户说的"还在运输中"对上）
    "A1867": {
        "order_id": "A1867", "customer": "李大壮", "phone": "13700003333",
        "placed_on": "2026-08-30", "item": "篮球（1 个）", "paid": 128.0,
        "status": "shipped",
    },
    # 10 —— 用户嘴上说"品控问题"，其实自己碰坏的
    "O9777": {
        "order_id": "O9777", "customer": "张老八", "phone": "13600004444",
        "placed_on": "2026-09-18", "item": "无线鼠标", "paid": 79.0,
        "status": "delivered",
    },
    # 11（待加）—— 超期单：单号真、姓名对、身份能核对，但下单 259 天了
    "B0001": {
        "order_id": "B0001", "customer": "李四", "phone": "13500005555",
        "placed_on": "2026-01-05", "item": "保温杯", "paid": 89.0,
        "status": "delivered",
    },
    # 备用：已退过款的（暂时没题用，留着给以后的题）
    "C9999": {
        "order_id": "C9999", "customer": "李佳峻", "phone": "13812345678",
        "placed_on": "2026-09-01", "item": "台灯", "paid": 159.0,
        "status": "refunded",
    },
}

# --------------------------------------------------------------------------
# 退款原因优先级表（你设计的，我把它落成数据）
#   级别越小 = 优先级越高
#
#   你已确认的处理规则（①号问题，甲方案）：
#       商家问题（级别 1）      → agent 可以自己处理
#       运输问题 / 买家问题      → **一律提交人工**，agent 不许自己退
# --------------------------------------------------------------------------

REASONS: dict[str, tuple[str, int, str]] = {
    # code:                (大类,      级别, 说明)
    "wrong_item":          ("商家问题", 1, "发错货"),
    "wrong_qty":           ("商家问题", 1, "数量不对"),
    "damaged_product":     ("商家问题", 1, "产品损坏"),
    "damaged_shipping":    ("运输问题", 2, "运输途中损坏"),
    "slow_shipping":       ("运输问题", 2, "运输时间长"),
    "lost_parcel":         ("运输问题", 2, "包裹丢失不到货"),
    "buyer_damage":        ("买家问题", 3, "个人原因导致损坏"),
    "not_satisfied":       ("买家问题", 3, "使用后不满意"),
    "malicious":           ("买家问题", 3, "恶意退款"),
}

# --------------------------------------------------------------------------
# 交卷用的三格答案（已定案）
#
#   订单状况 —— 这**订单本身能不能受理**：
#               查得到 + 没退过款 + 没超 30 天 + 身份核对通过  → 正常
#               任一条不满足                                    → 异常
#               （注意：跟"该不该退"是两件事。用户自己把东西弄坏了，
#                 订单本身是正常的，只是不该自动退 → 状况=正常、情况=提交人工）
#
#   退款原因 —— 商家 / 运输 / 个人 / 未知（判不出来就填未知）
#
#   退款情况 —— 允许 / 拒绝 / 提交人工
# --------------------------------------------------------------------------

CONCLUDE_FIELDS: dict[str, list[str]] = {
    "order_status": ["正常", "异常"],
    "reason": ["商家", "运输", "个人", "未知"],
    "action": ["允许", "拒绝", "提交人工"],
}


class RefundBackend(DialogueBackend):
    """假订单系统。"""

    # ②号问题：退款时限 —— 你定的 30 天
    WINDOW_DAYS = 30

    def __init__(self, today: str = TODAY, orders: dict | None = None):
        self.today = date.fromisoformat(today)
        self._orders = copy.deepcopy(orders if orders is not None else ORDERS)
        self.refunds: list[dict[str, Any]] = []      # 真正执行了的退款
        self.tickets: list[dict[str, Any]] = []      # 转人工的工单
        self.evidence: list[dict[str, Any]] = []     # 采集到的证据（**不判真伪**）
        self.verified: list[str] = []                # 已完成身份核对的单号
        self.denied: list[dict[str, Any]] = []       # 被拒的退款申请
        self.conclusion: dict[str, str] | None = None  # agent 交的卷

    # ---------------------------------------------------------------- 工具面

    def tool_specs(self) -> dict[str, dict[str, Any]]:
        return {
            "lookup_order": {
                "desc": "按订单号查系统里的订单：返回收货人、手机号、商品、下单时间、状态。状态以这里为准。",
                "args": {"order_id": "订单号"},
            },
            "verify_identity": {
                "desc": ("核对身份：把**用户口述的**完整姓名 + 手机号后四位提交上来，"
                         "跟订单收货人对齐。核对通过后本单才允许继续办。"),
                "args": {"order_id": "订单号", "full_name": "用户报出的完整姓名",
                         "phone_tail": "用户报出的手机号后四位"},
            },
            "classify_reason": {
                "desc": "查退款原因的归属：属于哪一类问题、优先级是几。",
                "args": {"reason_code": f"原因代码，可选: {sorted(REASONS)}"},
            },
            "collect_evidence": {
                "desc": ("采集用户提供的凭证编号（照片/视频的编号）。"
                         "你无法判断凭证是真是假，也不用判断 —— 只管收下来，真伪留给人工。"),
                "args": {"order_id": "订单号", "code": "凭证编号，例如 EV-1234"},
            },
            "issue_refund": {
                "desc": "执行退款。会按业务规则审核；不合规会被拒绝并说明理由。",
                "args": {"order_id": "订单号", "reason_code": "退款原因代码"},
            },
            "escalate": {
                "desc": "转人工处理。拿不准的、或规则规定不能自己办的事，用它。",
                "args": {"order_id": "订单号", "reason_code": "退款原因代码",
                         "note": "要交给人工判断的点"},
            },
            "conclude": {
                "desc": "交卷：给出你对这笔申请的三格结论。处理完（或决定拒绝）时调用。",
                "args": {
                    "order_status": f"订单状况，只能填: {CONCLUDE_FIELDS['order_status']}",
                    "reason": f"退款原因，只能填: {CONCLUDE_FIELDS['reason']}",
                    "action": f"退款情况，只能填: {CONCLUDE_FIELDS['action']}",
                },
            },
        }

    def call(self, tool: str, args: dict[str, Any]) -> str:
        try:
            if tool == "lookup_order":
                return self._lookup(str(args.get("order_id", "")))
            if tool == "verify_identity":
                return self._verify(str(args.get("order_id", "")),
                                    str(args.get("full_name", "")),
                                    str(args.get("phone_tail", "")))
            if tool == "classify_reason":
                return self._classify(str(args.get("reason_code", "")))
            if tool == "collect_evidence":
                return self._collect_evidence(str(args.get("order_id", "")),
                                              str(args.get("code", "")))
            if tool == "issue_refund":
                return self._refund(str(args.get("order_id", "")), str(args.get("reason_code", "")))
            if tool == "escalate":
                return self._escalate(str(args.get("order_id", "")),
                                      str(args.get("reason_code", "")),
                                      str(args.get("note", "")))
            if tool == "conclude":
                return self._conclude(args)
        except Exception as e:  # noqa: BLE001 —— 后端自己出错也要变成「告诉 agent 一声」，别炸掉整场对话
            return f"[后端异常] {type(e).__name__}: {e}"
        return f"[错误] 未知动作 {tool}"

    # ---------------------------------------------------------- 结构（写好了）

    def _order(self, order_id: str) -> dict[str, Any] | None:
        return self._orders.get(order_id.strip().upper())

    def _lookup(self, order_id: str) -> str:
        o = self._order(order_id)
        if o is None:
            return f"[查无此单] 系统里没有订单 {order_id!r}。"
        days = (self.today - date.fromisoformat(o["placed_on"])).days
        return (
            f"[已找到] 订单 {o['order_id']}\n"
            f"  收货人: {o['customer']}\n"
            f"  手机号: {o['phone']}\n"
            f"  商品: {o['item']}  金额: {o['paid']} 元\n"
            f"  下单: {o['placed_on']}（距今 {days} 天）\n"
            f"  状态: {o['status']}\n"
            f"  （这是系统里的记录。用户嘴上说的要跟它对上才算数。）"
        )

    def _verify(self, order_id: str, full_name: str, phone_tail: str) -> str:
        o = self._order(order_id)
        if o is None:
            return f"[核对失败] 系统里没有订单 {order_id!r}"
        name_ok = full_name.strip() == o["customer"]
        tail_ok = phone_tail.strip()[-4:] == o["phone"][-4:]
        if name_ok and tail_ok:
            if o["order_id"] not in self.verified:
                self.verified.append(o["order_id"])
            return "[核对通过] 与订单收货人一致。"
        miss = []
        if not name_ok:
            miss.append("姓名对不上")
        if not tail_ok:
            miss.append("手机尾号对不上")
        return f"[核对失败] {'、'.join(miss)}。可以让用户重新提供。"

    def _classify(self, code: str) -> str:
        item = REASONS.get(code.strip())
        if item is None:
            return f"[未知原因] {code!r} 不在原因表里。可选: {sorted(REASONS)}"
        cat, lvl, desc = item
        return f"[原因归属] {code} → {cat} / 优先级 {lvl} / {desc}"

    def _collect_evidence(self, order_id: str, code: str) -> str:
        """只采集，不判真伪 —— agent 没有判断凭证真假的能力，硬判没有意义。"""
        self.evidence.append({"order_id": order_id, "code": code})
        return (f"[已采集] 凭证编号 {code!r} 已记录在案，会随工单一起交给人工核实。\n"
                f"  （你不需要判断它的真伪。）")

    def _refund(self, order_id: str, code: str) -> str:
        o = self._order(order_id)
        if o is None:
            self.denied.append({"order_id": order_id, "why": "订单不存在"})
            return f"[退款被拒] 系统里没有订单 {order_id!r}"

        ok, why = self._decide(o, code)          # ← 规则在下面
        if not ok:
            self.denied.append({"order_id": o["order_id"], "code": code, "why": why})
            return f"[退款被拒] {why}"

        o["status"] = "refunded"
        self.refunds.append({"order_id": o["order_id"], "code": code,
                             "amount": o["paid"], "why": why})
        return f"[退款成功] {o['order_id']} 已退款 {o['paid']} 元。"

    def _escalate(self, order_id: str, code: str, note: str) -> str:
        self.tickets.append({"order_id": order_id, "code": code, "note": note})
        return f"[已转人工] 工单已建（订单 {order_id}，原因 {code}）。告用户会有人跟进。"

    def _conclude(self, args: dict[str, Any]) -> str:
        """收 agent 交的卷。词表不对就当场打回 —— 不许含糊。"""
        got: dict[str, str] = {}
        for field, allowed in CONCLUDE_FIELDS.items():
            val = str(args.get(field, "")).strip()
            if val not in allowed:
                return (f"[交卷被拒] {field} 填的是 {val!r}，不在允许的取值里。\n"
                        f"  只能填: {allowed}")
            got[field] = val
        self.conclusion = got
        return f"[已交卷] {got}"

    # ==================================================================
    # 核心业务规则 —— 这部分是你的
    # ==================================================================

    def _decide(self, order: dict[str, Any], code: str) -> tuple[bool, str]:
        """判断这一单能不能由 agent **直接退款**。返回 (能否退款, 理由)。

        你已经定死的规则（照这个写）：

          1. 已退过款的（order["status"] == "refunded"）→ 不能再退
          2. 时限：用 (self.today - 下单日期).days 跟 WINDOW_DAYS（30）比，超了 → 不能退
          3. 身份：order["order_id"] 不在 self.verified 里 → 不能退（没核对 = 越权）
          4. 原因：REASONS[code] 给出 (大类, 级别, 说明)。
             **只有「商家问题」允许直接退**；运输问题 / 买家问题一律不能退 ——
             这种情况要让 agent 走 escalate 转人工，而不是在这里放行。
          5. 原因为空/不认识 → 不能退

        ⚠ 只回答「**能不能**退」。
          agent「**该不该**主动退、还是该转人工」，由判分器判（转人工不算错，擅自退才算错）。
          两件事分开想。

        下面这几行是**占位实现**：只写了 1 和 3，2/4/5 故意没做。
        它能跑，但**不算你的答案**。替换掉它。
        """
        oid = order["order_id"]

        # 占位：已退过款的不能再退
        if order["status"] == "refunded":
            return False, "该订单此前已退款，不能重复退。"

        # 占位：没核对身份不允许退款
        if oid not in self.verified:
            return False, "尚未完成身份核对，不能办理退款。"

        # TODO(你)：2 时限 30 天
        # TODO(你)：4 只有「商家问题」放行，其余一律不能退
        # TODO(你)：5 原因不认识怎么办
        return True, "占位规则：已核对身份，直接放行（这不对，等你写）。"

    # ------------------------------------------------------------ 判分用快照

    def snapshot(self) -> dict[str, Any]:
        """判分器读这个。agent 拿不到。"""
        return {
            "refunds": copy.deepcopy(self.refunds),
            "tickets": copy.deepcopy(self.tickets),
            "evidence": copy.deepcopy(self.evidence),
            "verified": list(self.verified),
            "denied": copy.deepcopy(self.denied),
            "conclusion": copy.deepcopy(self.conclusion),
            "order_status": {k: v["status"] for k, v in self._orders.items()},
        }
