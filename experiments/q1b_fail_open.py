# -*- coding: utf-8 -*-
"""实验 Q1-b：异常处理里那个 return，决定判分器会不会被骗过去。

场景：
    任务要求 agent 在 result.txt 里写一个 SUCCESS 标记。
    这个 agent 什么都没干，还把文件删了。

你的任务：
    把下面 check_放水() 的 return 值改掉，
    让「删掉文件的 agent」被判成 **通过**。
"""
import os
import shutil
import tempfile

工作目录 = tempfile.mkdtemp(prefix="q1b_")
结果文件 = os.path.join(工作目录, "result.txt")


def 模拟_agent():
    """agent 什么正事都没干，顺手把文件删了（不好好干活的典型）"""
    if os.path.exists(结果文件):
        os.remove(结果文件)


def check_放水(路径):
    """❌ 你的任务：改这里，让它出错时"放行" """
    try:
        return "SUCCESS" in open(路径, encoding="utf-8").read()
    except Exception:
        return True          # ← TODO: 填什么值，能让骗子过关？


def check_严格(路径):
    """✅ 参考写法：出错就拦住"""
    try:
        return "SUCCESS" in open(路径, encoding="utf-8").read()
    except Exception:
        return False


if __name__ == "__main__":
    模拟_agent()
    print("agent 删掉了 result.txt，什么都没做。\n")

    for 名字, 检查 in [("check_放水", check_放水), ("check_严格", check_严格)]:
        try:
            判定 = 检查(结果文件)
            尾巴 = "   ← 骗子过关了" if 判定 else "   ← 正确拦住"
            print(f"  {名字}: 判定 = {判定}{尾巴}")
        except Exception as e:
            print(f"  {名字}: 崩了 ({type(e).__name__}) —— 整场考试作废")

    shutil.rmtree(工作目录, ignore_errors=True)
