"""附加题：同一个 ScriptedUser 对象被复用，会发生什么？

跑法：py experiments/q2b_user_reuse.py
"""
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.users import ScriptedUser

SCRIPT = ["我上周买的鞋不合脚，能退吗？", "订单号是 A1234"]


def run_one_repeat(user, label):
    """跑一轮对话，返回这一轮用户说过的话。"""
    print(f"\n--- {label} ---")
    said = []
    for turn in range(1, 8):
        msg = user.next_message(list(said))
        print(f"  第{turn}轮  用户 → {msg!r}")
        if msg is None:
            print("          （用户没话了，对话结束）")
            break
        said.append(msg)
    return said


print("=" * 64)
print("场景：评测要对同一个任务跑 3 次重复，看成绩稳不稳")
print("=" * 64)

print("\n########## 写法 A：三次共用一个对象 ##########")
shared = ScriptedUser(SCRIPT)
for i in (1, 2, 3):
    said = run_one_repeat(shared, f"第 {i} 次重复")
    print(f"  → 这一轮用户说了 {len(said)} 句")

print("\n########## 写法 B：每次重复新建一个 ##########")
for i in (1, 2, 3):
    fresh = ScriptedUser(SCRIPT)
    said = run_one_repeat(fresh, f"第 {i} 次重复")
    print(f"  → 这一轮用户说了 {len(said)} 句")

print()
print("=" * 64)
print("为什么")
print("=" * 64)
print("""
ScriptedUser 身上有两样东西：

    script —— 剧本内容    不会变
    index  —— 说到第几句  会变，而且只进不退

代码里没有「归零」这一步。第 1 次跑完，index 已经停在末尾。
第 2 次进来，第一次调用就撞上守卫 → 立刻 return None
→ 用户全程沉默 → agent 拿不到任何信息 → 这一轮必然失败。

真正危险的是：它不报错。
程序正常退出、退出码 0，只是成绩默默变差。
你不去对比 3 次重复的数字，根本发现不了。

跟 Q2 的 state_hash 是同一件事的两面：
    state_hash 管「考场环境」每次开考一致不一致
    这里管     「用户模拟器」每次开考是不是从干净起点出发
两者都指向同一句：每次重复的起点不一致，成绩单就是假的。
""")
