"""对话型评测：用户模拟器。

工具型任务里，agent 面对的是一堆死文件——发一条指令，它自己折腾完交卷。
对话型任务不一样：agent 要跟一个"活的用户"来回聊，用户会回应、会追问、
问完满意了才说"行，那就这样"。

所以这类任务需要两个新角色：

    1. UserSimulator —— 扮演用户                        ← 这个文件
    2. 假后端        —— agent 的动作真正改到的东西        ← 下一步做

先做 ScriptedUser：按固定剧本说话，不花钱、每次一样、能验证。
LLM 版用户以后再加 —— 规矩不变：先把能被验证的东西造出来。
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class UserSimulator(ABC):
    """用户模拟器接口。考场只认这一个方法。"""

    name: str = "user"

    @abstractmethod
    def next_message(self, history: list[str]) -> str | None:
        """给定目前的对话历史，返回用户下一句话。

        返回 None 表示：用户没话说了，对话结束。
        """


class ScriptedUser(UserSimulator):
    """按剧本说话的用户。剧本是一串句子，说完就结束。

    用法（写完你的实现之后，这几行应该都能跑对）：

        user = ScriptedUser(["我上周买的鞋不合脚，能退吗？", "订单号是 A1234"])
        user.next_message([])                            -> "我上周买的鞋不合脚，能退吗？"
        user.next_message(["我上周买的鞋..."])             -> "订单号是 A1234"
        user.next_message(["...", "..."])                -> None      ← 剧本说完了
    """

    def __init__(self, script: list[str]):
        self.script = list(script)
        # TODO(你) ①
        # 这里还缺一个东西：记住"已经说到第几句了"。
        # 想个名字，给它一个初值，加一行。
        # 提示：一个整数；第一句之前，你还没说过任何话。
        #       注意别写成 self.script[0] —— 我们要记的是"位置"，不是"内容"。

    def next_message(self, history: list[str]) -> str | None:
        # TODO(你) ②  下面三步，把这行 raise 删掉换成你的实现
        #
        #   1. 先守住边界：如果已经说到剧本末尾了，直接 return None
        #      （这种"先判断、提前返回"的写法叫 guard clause，
        #        比 if/else 套来套去清爽得多，是 Python 里的好习惯）
        #
        #   2. 把当前这一句取出来，存到一个变量里
        #
        #   3. 游标往前挪一格，然后 return 那句话
        #
        #   顺序很关键：如果先挪游标再取句子，你会取错一格。
        #   自己想一想为什么，想通了再写。
        raise NotImplementedError("轮到你了 —— 删掉这一行，写上你的实现")


# ---------------------------------------------------------------------------
# 自检：写完直接跑，看有没有 ✗
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("ScriptedUser 自检\n")
    ok = True

    u = ScriptedUser(["第一句", "第二句"])

    got = u.next_message([])
    good = got == "第一句"
    ok = ok and good
    print(f"  {'v' if good else 'X'} 第一次调用 → {got!r}   （期望 '第一句'）")

    got = u.next_message(["第一句"])
    good = got == "第二句"
    ok = ok and good
    print(f"  {'v' if good else 'X'} 第二次调用 → {got!r}   （期望 '第二句'）")

    got = u.next_message(["第一句", "第二句"])
    good = got is None
    ok = ok and good
    print(f"  {'v' if good else 'X'} 第三次调用 → {got!r}      （期望 None：剧本说完了）")

    e = ScriptedUser([])
    got = e.next_message([])
    good = got is None
    ok = ok and good
    print(f"  {'v' if good else 'X'} 空剧本     → {got!r}      （期望 None）")

    print()
    print("自检通过 --- 可以把这段贴给我看" if ok else "还没通过，看看上面哪行是 X")

    # -----------------------------------------------------------------------
    # 加餐（选做，想到了就赚到）：
    #
    # 这个对象身上带着状态 —— "说到第几句了"。这个状态会随着调用往前走。
    #
    # 想一想：如果评测要对同一个任务跑 3 次重复，
    #         而 harness 复用了同一个 ScriptedUser 对象 —— 第 2 次会发生什么？
    #
    # 提示：这跟 Q2 里 state_hash 管的那件事，是同一类问题。
    # -----------------------------------------------------------------------
