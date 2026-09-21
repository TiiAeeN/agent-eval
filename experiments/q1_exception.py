# -*- coding: utf-8 -*-
"""实验：一条坏 check 会不会带走整场考试？

对应问题 Q1。模拟场景：
  任务 A / B / C 各有一个文件，check 去读它。
  任务 B 的文件被 agent 写坏了（非法 JSON）。
"""
import json

TASKS = [
    ("任务A", '{"port": 8080}'),
    ("任务B", '{ 这不是合法的 json'),   # ← agent 把文件写烂了
    ("任务C", '{"port": 9090}'),
]


def check_裸奔(value):
    """没有 try/except 的版本"""
    return json.loads(value)["port"]


def check_带保护(value):
    """有 try/except 的版本"""
    try:
        return json.loads(value)["port"]
    except Exception as e:
        return 0


def 跑(名字, check):
    print(f"=== {名字} ===")
    结果 = []
    try:
        for 任务, 内容 in TASKS:
            结果.append((任务, check(内容)))
    except Exception as e:
        print(f"  ✗ 跑到「{任务}」时崩了：{type(e).__name__}: {e}")
    print(f"  拿到 {len(结果)} / {len(TASKS)} 个任务的结果")
    for 任务, r in 结果:
        print(f"    {任务}: {r}")
    print()


if __name__ == "__main__":
    跑("不包 try/except", check_裸奔)
    跑("包了 try/except", check_带保护)
