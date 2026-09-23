"""为什么 Python 里"前面多几个空格"会直接报错？

关键：报错发生在【编译期】—— 一行都还没执行。
这个脚本把四种写法分别交给 Python 编译，看它怎么说。

跑法：py experiments/q3_indent_demo.py
"""
import subprocess
import sys

CASES = [
    (
        "例1：同一个块里，缩进统一是 10 格",
        "def f():\n"
        "          return 1\n"
        'print("  跑通了，返回", f())\n',
    ),
    (
        "例2：同一个块里，一行 4 格、一行 10 格",
        "def f():\n"
        "    a = 1\n"
        "          return a\n"
        "print(f())\n",
    ),
    (
        "例3：没有冒号，却往里缩了一层",
        "x = 1\n"
        "    y = 2\n"
        "print(x + y)\n",
    ),
    (
        "例4：Tab 和空格混着用",
        "def f():\n"
        "\tx = 1\n"
        "        y = 2\n"
        "        return x + y\n"
        "print(f())\n",
    ),
]


def visible(code: str) -> str:
    """把空格画成 · ，Tab 画成 → ，让缩进看得见。"""
    return code.replace(" ", "\u00b7").replace("\t", "\u2192")


for title, code in CASES:
    print("=" * 62)
    print(title)
    print("=" * 62)
    print("  源码（空格=·，Tab=→）：")
    for line in code.splitlines():
        print(f"      {visible(line)}")
    print()
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    if r.returncode == 0:
        print("  结果：✅ 没报错")
        for line in r.stdout.strip().splitlines():
            print("        ", line)
    else:
        print("  结果：❌ 报错（编译期，一行都没跑）")
        for line in r.stderr.strip().splitlines()[-2:]:
            print("        ", line.strip())
    print()

print("=" * 62)
print("一句话结论")
print("=" * 62)
print("""
Python 不在乎你缩 2 格、4 格还是 10 格 —— 例 1 就用了 10 格，照样跑通。
它在乎的是：**同一个块里的缩进必须完全一致**。

例 1 能跑 / 例 2、3、4 报错，这四个例子的差别就在这。
""")
